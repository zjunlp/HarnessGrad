#!/usr/bin/env python3
"""Point a harness's declared runtime paths at what this machine actually has.

    python3 tools/point_harness.py base_harness/codex base_harness/openclaw   # report
    python3 tools/point_harness.py base_harness/codex --write                 # rewrite

Why this exists
---------------
A CLI harness declares the trees the sandbox must bind read-only (`runtime_paths`), and
those trees are where a Node runtime and its installed packages live. On this host that
spelling contains a Node version:

    /home/<user>/.nvm/versions/node/v22.16.0/lib/node_modules/@openai/codex/...

which is correct until the machine installs a different Node. The manifest is then wrong in
a way that fails loudly but confusingly (`shutil.which` finds nothing inside the sandbox,
`eval/containers/sandbox.py` silently skips a bind whose target does not exist, and the
harness dies on its first exec). Measured 2026-11-01: four manifests pin `v22.16.0` or
`v22.19.0` on a host that also has `v22.23.4`.

Rewriting it by hand each time is how a wrong path survives a review. This tool does the
one mechanical thing: find the same *tail* under a sibling version directory and say so.
It is deliberately not a resolver the harness runs at task time -- what a subject may see
is part of the sandbox plan, and a plan that changes with the host's Node version is a plan
that cannot be quoted in a paper.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

#: A version-looking path component. `v22.16.0` (nvm) and `22.16.0` both appear.
_VERSION = re.compile(r"^v?\d+\.\d+\.\d+$")


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", name))


def resolve(path: Path) -> Path | None:
    """The same tree under a sibling version directory, preferring the newest.

    `None` means "no sibling has it", which is a real answer: the package is not installed
    under any version this tool can see, and the operator has to install it.
    """
    if path.exists():
        return path
    parts = path.parts
    for index, part in enumerate(parts):
        if not _VERSION.match(part):
            continue
        prefix, tail = Path(*parts[:index]), Path(*parts[index + 1:])
        try:
            candidates = [prefix / sibling / tail for sibling in prefix.iterdir()
                          if _VERSION.match(sibling.name)]
        except OSError:
            continue
        found = [candidate for candidate in candidates if candidate.exists()]
        if found:
            return max(found, key=lambda candidate: _version_key(candidate.parts[index]))
    return None


def check(manifest: Path) -> list[dict]:
    """One row per declared path: what it says, what this machine has, and whether it moved."""
    spec = json.loads(manifest.read_text(encoding="utf-8"))
    rows = []
    for declared in spec.get("runtime_paths") or []:
        path = Path(str(declared))
        target = resolve(path)
        rows.append({"declared": str(declared),
                     "resolved": str(target) if target else None,
                     "state": "ok" if target == path else ("moved" if target else "missing")})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("harnesses", nargs="+", type=Path)
    parser.add_argument("--write", action="store_true",
                        help="rewrite harness.json with the resolved paths")
    args = parser.parse_args(argv)
    status = 0
    for harness in args.harnesses:
        manifest = harness / "harness.json"
        if not manifest.is_file():
            print(f"{harness}: no harness.json", file=sys.stderr)
            status = 2
            continue
        rows = check(manifest)
        moved = [row for row in rows if row["state"] != "ok"]
        print(f"{harness.name}: {len(rows)} declared path(s), "
              + (f"{len(moved)} to fix" if moved else "all resolve"))
        for row in rows:
            if row["state"] == "ok":
                continue
            status = 1
            print(f"  {row['state']:8s} {row['declared']}")
            if row["resolved"]:
                print(f"           -> {row['resolved']}")
        if args.write and moved:
            spec = json.loads(manifest.read_text(encoding="utf-8"))
            spec["runtime_paths"] = [row["resolved"] or row["declared"] for row in rows]
            manifest.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
            print(f"  rewrote {manifest}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
