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


def configure(harness: Path, dsh_root: Path, node: str = "node") -> dict:
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
    bindings = list(dict.fromkeys([str(selected["root"]), str(executable), str(node_root)]))
    manifest["runtime_paths"] = bindings
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
            "farm_targets": len(entries), "runtime_paths": bindings}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", type=Path)
    parser.add_argument("--dsh-root", required=True, type=Path)
    parser.add_argument("--node", default="node", help="Node executable, resolved once during preparation")
    args = parser.parse_args(argv)
    try:
        receipt = configure(args.harness, args.dsh_root, args.node)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
