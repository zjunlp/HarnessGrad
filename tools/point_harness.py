#!/usr/bin/env python3
"""Point a harness's declared runtime paths at what this machine actually has.

    python3 tools/point_harness.py path/to/harness           # report
    python3 tools/point_harness.py path/to/harness --write   # rewrite

The sandbox uses `runtime_paths` in `harness.json` to bind runtime trees read-only.
For a missing path that contains a version directory, this tool looks for the same
relative path under sibling version directories and reports the newest match.
Pass `--write` to record the resolved paths in the manifest before running the harness.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

#: A numeric version directory can start with `v`.
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
