#!/usr/bin/env python3
"""Record a local DSH installation in a Harness manifest.

The prepared paths are part of the candidate identity and travel into task
sandboxes as declared read-only bindings. No dependencies are installed here.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.containers import sandbox
from harnessgrad.paths import main_checkout

TEMPLATE = ROOT / "base_harness/deepseek-harness"


def _helper(harness: Path):
    for name in ("harness.json", "agent.py", "dsh_runtime.py"):
        path = harness / name
        if not path.is_file() or not path.resolve().is_relative_to(harness):
            raise ValueError(f"DSH candidate needs its owned {name}")
    spec = importlib.util.spec_from_file_location("_configured_dsh_identity", TEMPLATE / "dsh_runtime.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _probe_npm(record: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="hg-dsh-probe-") as temporary:
        root = Path(temporary)
        work, task = root / "work", root / "task"
        work.mkdir()
        task.mkdir()
        env = {"PATH": os.defpath, "HOME": str(task), "TMPDIR": str(task),
               "DSH_HOME": str(task / ".dsh"), "NARB_DISABLE_NATIVE_CACHE": "1",
               "DSH_TELEMETRY_DISABLED": "1"}
        for options in (["--version"], ["--profile", "headless", "--help"]):
            command = sandbox.command([*record["argv"], *options], platform=ROOT,
                                      work_root=work, repo=work, task_dir=task, env=env, network="none",
                                      runtime_paths=tuple(map(Path, record["runtime_paths"])))
            result = subprocess.run(command, cwd=task, env=env, input="", capture_output=True,
                                    text=True, errors="replace", timeout=45)
            if result.returncode or not result.stdout.strip():
                raise ValueError(f"DSH sandbox probe {options} failed: "
                                 f"{(result.stderr or result.stdout)[-1200:]}")
            if options == ["--version"] and result.stdout.strip() != record["version"]:
                raise ValueError(f"DSH CLI version mismatch: expected {record['version']}, "
                                 f"found {result.stdout.strip()}")


def _runtime_directory(path: Path, label: str) -> None:
    """Require an isolated tree that survives both supported sandbox layouts."""
    # A system Node's inferred prefix is usually /usr or /usr/local. Binding it
    # into a task image would replace that image's Python, libraries and tools.
    # Keep installations outside system trees, including their symlink aliases.
    system = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/dev", "/proc", "/sys")
    if len(path.parts) < 3 or any(path.is_relative_to(tree) for tree in system):
        raise ValueError(f"{label} must use a dedicated installation directory outside system trees: {path}; "
                         "select an isolated Node distribution with --node and a separate DSH checkout")
    if path.is_relative_to("/tmp"):
        raise ValueError(f"{label} is under /tmp, which the native sandbox hides: {path}; "
                         "move the installation outside /tmp and prepare the Harness again")
    if path.is_relative_to(ROOT) or ROOT.is_relative_to(path):
        raise ValueError(f"{label} must not contain or lie within the hidden platform checkout: {path}")


def configure(harness: Path, dsh_root: Path, node: str = "node", *,
              runtime_paths: tuple[Path, ...] = (), npm_version: str | None = None) -> dict:
    harness = harness.expanduser().resolve()
    runtime_helper = _helper(harness)
    path = harness / "harness.json"
    original = path.read_bytes()
    manifest = json.loads(original)
    if manifest.get("name") != "deepseek-harness" or manifest.get("entrypoint") != "agent.py":
        raise ValueError("expected the deepseek-harness agent.py manifest")
    if npm_version is None:
        spec = importlib.util.spec_from_file_location("_configured_dsh_agent", TEMPLATE / "agent.py")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        selected = adapter.runtime({"HG_AGENT_DSH_ROOT": str(dsh_root), "HG_AGENT_NODE": node,
                                    "PATH": os.environ.get("PATH", "")})
        entries = adapter.farm_targets(selected["root"], harness / "dsh_farm.tsv")
        runtime_helper.require_clean_startup(os.environ)
    else:
        root = dsh_root.expanduser().resolve(strict=True)
        cli = root / runtime_helper.NPM_CLI
        if not cli.is_file() or cli.resolve() != cli:
            raise ValueError(f"DSH npm launcher must be owned by the installation: {cli}")
        selected = {"root": root, "node": str(Path(node).resolve(strict=True)),
                    "cli": str(cli), "layout": "npm"}
        entries = []
    executable = Path(selected["node"])
    node_root = executable.parent.parent if executable.parent.name == "bin" else executable.parent
    _runtime_directory(selected["root"], "DSH root")
    _runtime_directory(node_root, "Node installation")
    bindings = list(dict.fromkeys([str(selected["root"]), str(executable), str(node_root)]))
    for extra in runtime_paths:
        value = str(extra.expanduser().resolve(strict=True))
        if value not in bindings:
            bindings.append(value)
    record = runtime_helper.installation(harness, selected, bindings, entries,
                                          forbidden=(ROOT, main_checkout(ROOT), harness))
    if npm_version is not None:
        if record["version"] != npm_version:
            raise ValueError(f"DSH npm version mismatch: expected {npm_version}, found {record['version']}")
        _probe_npm(record)
    runtime_helper.check(record)
    if path.read_bytes() != original:
        raise ValueError("DSH manifest changed during preparation")
    manifest["runtime_paths"] = bindings
    manifest[runtime_helper.RECORD] = record
    # Validation finishes before writing; replace one complete manifest so a
    # failing preparation never leaves partially written JSON behind.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=harness, prefix=".dsh-manifest-",
                                         encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(manifest, indent=2) + "\n")
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"harness": str(harness), "dsh_root": str(selected["root"]),
            "cli": selected["cli"], "node": selected["node"],
            "farm_targets": len(entries), "runtime_paths": bindings, "identity": record}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", type=Path)
    parser.add_argument("--dsh-root", required=True, type=Path)
    parser.add_argument("--node", default="node", help="Node executable, resolved once during preparation")
    parser.add_argument("--runtime", type=Path, action="append", default=[],
                        help="dedicated external dependency file or directory (repeatable)")
    args = parser.parse_args(argv)
    try:
        receipt = configure(args.harness, args.dsh_root, args.node, runtime_paths=tuple(args.runtime))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
