"""Attach prepared dependencies to a CLI candidate before launching its improver."""
from __future__ import annotations
import json
import shlex
import sys
import tempfile
from pathlib import Path

# The sandbox exposes this single stdlib-only checkpoint helper read-only. It
# contains no scorer, task data or platform entrypoint; the rest of ckpt is hidden.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.ckpt.bun_dependencies import attach_fresh, dependency_identity, _module_dirs


def source_for(candidate, receipt):
    manifest = candidate / "harness.json"
    if not manifest.is_file():
        return None
    spec = json.loads(manifest.read_text()).get("runtime")
    if spec is None:
        return None
    if not receipt or receipt.get("kind") != "bun":
        raise ValueError("Candidate requires a prepared Bun dependency receipt")
    identity = receipt["identity"]
    if spec != identity["declaration"]:
        raise ValueError("Bun dependency declaration changed; prepare dependencies first")
    source = (candidate / spec["source"]).resolve()
    if not source.is_relative_to(candidate.resolve()) or not source.is_dir():
        raise ValueError("Bun source must belong to the candidate")
    key = dependency_identity(source, identity["version"], identity["system"], identity["arch"],
                              package=spec["package"], binary_sha256=identity["binary_sha256"])
    if key != receipt["dependency_key"]:
        raise ValueError("Bun dependency inputs changed; prepare dependencies first")
    return source


def stage_candidate(candidate, receipt):
    """Only called on the freshly copied tree, before any candidate code executes."""
    source = source_for(candidate, receipt)
    if source is None:
        return None
    store = Path(receipt["dependency_store"]).resolve()
    # Source links must not let layout creation write outside the new candidate.
    for p in source.rglob("*"):
        if p.is_symlink() and not p.resolve().is_relative_to(source):
            raise ValueError("Bun source link escapes candidate before dependency attachment")
    attach_fresh(source, store)
    for modules in _module_dirs(source):
        for link in modules.rglob("*"):
            if link.is_symlink() and not (link.resolve().is_relative_to(source) or
                                         link.resolve().is_relative_to(store)):
                raise ValueError("Bun workspace dependency escapes candidate and prepared store")
    state = candidate / ".state"
    state.mkdir(exist_ok=False)  # copy_harness excludes generated state
    (state / "candidate-runtime.json").write_text(json.dumps(receipt, indent=2) + "\n")
    # Tests can edit source, but must not choose mounts for the next invocation.
    # Keep the launch copy outside the candidate; its in-tree copy is informational.
    private = Path(tempfile.mkdtemp(prefix=f".{candidate.name}-selftest-", dir=candidate.parent))
    launch_receipt = private / "runtime.json"
    launch_receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    runner = Path(__file__).with_name("bun_selftest.py")
    command = shlex.join([sys.executable, str(runner), "--candidate", str(candidate),
                          "--runtime", str(launch_receipt)])
    entry = state / "self-test"
    entry.write_text('#!/bin/sh\nexec ' + command + ' "$@"\n')
    entry.chmod(0o755)
    return entry
