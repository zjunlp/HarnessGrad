#!/usr/bin/env python3
"""Semantic three-way merge for docs/dataset-blockers.json.

WHY THIS EXISTS
Resolving this file's merge conflicts by reconstructing text from `git`'s conflict
markers dropped a `path` field, a closing brace or an object separator six times in
one phase. Text reconstruction is the wrong tool: the file is JSON, and the merge is
a merge of *values*. This script parses the three versions and merges the values, so
the output cannot be malformed by construction.

USAGE (inside a cherry-pick/rebase that conflicted on the registry)
    python3 tools/merge_blocker_registry.py <base> <ours> <theirs> <output>

`base`/`ours`/`theirs` may be either a path or a git rev (e.g. `:2:docs/...`).
It exits non-zero and writes nothing if any input is not valid JSON, or if a merge
would have to discard a value it cannot merge - losing evidence silently is worse
than failing loudly.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LIST_OF_TEXTS = ("notes",)


def load(ref: str) -> dict:
    if ref.startswith(":") or "/" not in ref or not Path(ref).exists():
        out = subprocess.run(["git", "show", ref], capture_output=True, text=True, errors="replace")
        if out.returncode != 0:
            raise SystemExit(f"cannot read {ref!r}: {out.stderr.strip()}")
        text = out.stdout
    else:
        text = Path(ref).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{ref!r} is not valid JSON: {exc}") from exc


def merge_text(base: str, ours: str, theirs: str) -> str:
    """Keep both edits to one string, trimming the shared prefix."""
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    n = 0
    while n < min(len(ours), len(theirs)) and ours[n] == theirs[n]:
        n += 1
    cut = max(ours.rfind(". ", 0, n), ours.rfind("; ", 0, n))
    cut = cut + 2 if cut != -1 else n
    rest = theirs[cut:].strip()
    return ours if not rest else ours.rstrip() + " " + rest


def merge_text_list(base: list, ours: list, theirs: list) -> list:
    """Ordered union: base order, then whichever side added what, de-duplicated."""
    out: list = []
    for item in list(base) + list(ours) + list(theirs):
        if item not in out:
            out.append(item)
    return out


def merge_evidence(base: list, ours: list, theirs: list) -> list:
    """Union of evidence objects keyed by (kind, path, locator)."""
    out: list = []
    seen: set = set()

    def key(item):
        if not isinstance(item, dict):
            return ("!raw", json.dumps(item, sort_keys=True))
        return (
            item.get("kind"),
            item.get("path") or item.get("record") or "",
            item.get("locator") or "",
        )

    for item in list(base) + list(ours) + list(theirs):
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def merge_object(base: dict, ours: dict, theirs: dict, where: str) -> dict:
    out: dict = {}
    for field in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(field), ours.get(field), theirs.get(field)
        if field == "evidence":
            out[field] = merge_evidence(b or [], o or [], t or [])
        elif field in ("observed", "next_action") and isinstance(o, str) and isinstance(t, str):
            out[field] = merge_text(b or "", o, t)
        elif field == "notes":
            out[field] = merge_text_list(b or [], o or [], t or [])
        elif o == t:
            out[field] = o
        elif o == b:
            out[field] = t
        elif t == b:
            out[field] = o
        else:
            raise SystemExit(
                f"{where}: field {field!r} changed on both sides and this script "
                f"cannot merge it safely; resolve it by hand rather than losing a value"
            )
    return out


def merge_list_of_objects(base: list, ours: list, theirs: list, keyfield: str, where: str) -> list:
    def index(items):
        return {item.get(keyfield): item for item in items if isinstance(item, dict)}

    b, o, t = index(base), index(ours), index(theirs)
    out = []
    for name in [item.get(keyfield) for item in list(base) + list(ours) + list(theirs)]:
        if name in (x.get(keyfield) for x in out):
            continue
        if name not in o and name not in t:
            continue
        out.append(merge_object(b.get(name, {}), o.get(name, {}), t.get(name, {}), f"{where}[{name}]"))
    return out


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    base, ours, theirs = (load(r) for r in sys.argv[1:4])
    output = sys.argv[4]
    merged: dict = {}
    for field in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(field), ours.get(field), theirs.get(field)
        if field == "shared_blockers":
            merged[field] = merge_list_of_objects(b or [], o or [], t or [], "id", "shared_blockers")
        elif field == "datasets":
            merged[field] = merge_list_of_objects(b or [], o or [], t or [], "dataset", "datasets")
        elif field == "review":
            merged[field] = merge_object(b or {}, o or {}, t or {}, "review")
        elif o == t:
            merged[field] = o
        elif o == b:
            merged[field] = t
        elif t == b:
            merged[field] = o
        else:
            raise SystemExit(f"top-level field {field!r} changed on both sides; resolve by hand")
    text = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    Path(output).write_text(text, encoding="utf-8")
    print(f"merged -> {output} ({len(text)} bytes, {len(merged.get('shared_blockers', []))} shared blockers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
