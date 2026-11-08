#!/usr/bin/env python3
"""Record a local DSH installation in a Harness manifest, without running DSH.

The prepared paths are part of the candidate identity and travel into task
sandboxes as declared read-only bindings. No dependencies are installed here.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


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
              runtime_paths: tuple[Path, ...] = ()) -> dict:
    harness = harness.expanduser().resolve()
    path = harness / "harness.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("name") != "deepseek-harness" or manifest.get("entrypoint") != "agent.py":
        raise ValueError("expected the deepseek-harness agent.py manifest")
    spec = importlib.util.spec_from_file_location(
        "_configured_dsh_agent", ROOT / "base_harness/deepseek-harness/agent.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    selected = adapter.runtime({"HG_AGENT_DSH_ROOT": str(dsh_root), "HG_AGENT_NODE": node,
                                "PATH": os.environ.get("PATH", "")})
    entries = adapter.farm_targets(selected["root"], harness / "dsh_farm.tsv")
    executable = Path(selected["node"])
    node_root = executable.parent.parent if executable.parent.name == "bin" else executable.parent
    _runtime_directory(selected["root"], "DSH root")
    _runtime_directory(node_root, "Node installation")
    bindings = list(dict.fromkeys([str(selected["root"]), str(executable), str(node_root)]))
    for extra in runtime_paths:
        value = str(extra.expanduser().resolve(strict=True))
        if value not in bindings:
            bindings.append(value)
    runtime_spec = importlib.util.spec_from_file_location(
        '_configured_dsh_identity', ROOT / 'base_harness/deepseek-harness/dsh_runtime.py')
    runtime_helper = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime_helper)
    runtime_helper.require_clean_startup(os.environ)
    record = runtime_helper.installation(harness, selected, bindings, entries,
                                          forbidden=(ROOT, harness))
    runtime_helper.check(record)
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
