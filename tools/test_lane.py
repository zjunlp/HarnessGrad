#!/usr/bin/env python3
"""Run the tests a change can actually reach -- and say why each file was picked.

The full `tests/quality` suite is the right gate for a merge and the wrong gate for a
loop: measured 2026-10-19 on `mistral`, six minutes bought 7% of it. Most of that time is
spent proving that files the change never touched still work.

This tool does the mapping instead of guessing: every changed path is turned into the
names a test would have to mention to be about it (the dotted module path, the file name,
the adapter or dataset it belongs to), and only tests mentioning one of those names run.

    python3 tools/test_lane.py                  # what this branch changed, vs the merge base
    python3 tools/test_lane.py --base HEAD~1    # a different base
    python3 tools/test_lane.py --files a.py,b.py
    python3 tools/test_lane.py --list           # print the selection, run nothing
    python3 tools/test_lane.py --full           # the whole suite (the slow, honest gate)

Selection is deliberately conservative in one direction: a changed file that matches no
test is reported as **unmatched**, not silently dropped, because "nothing tests this" is a
fact the person merging needs to see. `--full` exists for the case where that matters.

Tests are run with a `--basetemp` outside the platform tree (the runner refuses a work root
inside it), and the whole lane is bounded by `--max-seconds` so a loop cannot turn into a
night.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Changed files under these roots never select a test: they are output, not code.
#: Matched on the first path component.
NON_CODE = {
    "docs", "website", "reports", "registry", "assets", "runs", "contrib",
    "current_harness", "legacy", "web",
}

#: Tokens too generic to mean anything: a test mentioning "run" is not about `run.py`.
STOPWORDS = {
    "test", "tests", "main", "core", "base", "util", "utils", "init", "index", "cli",
    "api", "run", "runner", "data", "eval", "tools", "model", "config", "common",
}

#: `tests/quality/test_<token>.py`-style files count only if the token is at least this long.
MIN_TOKEN = 4


def module_tokens(path: str) -> list[str]:
    """The names a test would have to mention to be about this file.

    `data/datasets/gsm8k.py` -> `data.datasets.gsm8k`, `datasets.gsm8k`, `gsm8k`.
    The shortest of those is what most tests actually write, which is why it is included;
    the dotted form is what an import looks like, which is why it is preferred.
    """
    p = pathlib.PurePosixPath(path)
    stem = p.stem
    parts = list(p.with_suffix("").parts)
    tokens: list[str] = []
    if len(parts) >= 2 and parts[-1] in ("__init__",):
        parts = parts[:-1]
    dotted = ".".join(parts)
    if dotted:
        tokens.append(dotted)
    if len(parts) >= 2:
        tokens.append(".".join(parts[-2:]))
    if stem and stem != "__init__":
        tokens.append(stem)
    out = []
    for token in tokens:
        if len(token) < MIN_TOKEN or token.lower() in STOPWORDS:
            continue
        if token not in out:
            out.append(token)
    return out


def plan(changed: list[str], tests: list[pathlib.Path]) -> tuple[dict[pathlib.Path, list[str]],
                                                               list[str]]:
    """(tests to run -> why, changed paths no test mentions)."""
    selected: dict[pathlib.Path, list[str]] = {}
    unmatched: list[str] = []
    by_path = {str(t.resolve()): t for t in tests}
    for name in changed:
        parts = pathlib.PurePosixPath(name).parts
        first = parts[0] if len(parts) > 1 else ""
        if first in NON_CODE or first.startswith("."):
            continue
        # A changed **test** is directly the thing to run; it needs no mention to be matched.
        if name.startswith("tests/") and name.endswith(".py"):
            selected.setdefault(ROOT / name, []).append("changed itself")
            continue
        tokens = module_tokens(name)
        hits = []
        for test in tests:
            text = test.read_text(encoding="utf-8", errors="replace")
            matched = [t for t in tokens if t in text]
            if matched:
                hits.append((test, matched[0]))
        if not hits:
            unmatched.append(name)
            continue
        for test, token in hits:
            selected.setdefault(test, [])
            if token not in selected[test]:
                selected[test].append(token)
    return selected, unmatched


def changed_files(base: str) -> list[str]:
    """Committed changes since the merge base, plus everything not committed yet."""
    names: list[str] = []
    merge_base = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=False)
    spec = f"{merge_base.stdout.strip()}...HEAD" if merge_base.returncode == 0 else base
    diff = subprocess.run(["git", "diff", "--name-only", spec], cwd=ROOT,
                          capture_output=True, text=True, check=False)
    if diff.returncode == 0:
        names += [line for line in diff.stdout.splitlines() if line.strip()]
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            if not line.strip():
                continue
            rest = line[3:]
            if " -> " in rest:                       # a rename: both sides matter
                names += [part.strip() for part in rest.split(" -> ")]
            else:
                names.append(rest.strip().strip('"'))
    seen, out = set(), []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def test_files() -> list[pathlib.Path]:
    tests = ROOT / "tests"
    return sorted(p for p in tests.rglob("test_*.py") if "__pycache__" not in p.parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main",
                    help="git ref to diff against (default: origin/main, then its merge base)")
    ap.add_argument("--files", default=None,
                    help="comma-separated changed paths, instead of asking git")
    ap.add_argument("--list", action="store_true", help="print the selection, run nothing")
    ap.add_argument("--full", action="store_true", help="run the whole suite instead")
    ap.add_argument("--max-seconds", type=int, default=600,
                    help="wall-clock bound for the lane (default 600)")
    args = ap.parse_args()

    if args.full:
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   str(ROOT / "tests" / "quality")]
        print("lane: full")
        return _run(command, args.max_seconds)

    changed = ([p.strip() for p in args.files.split(",") if p.strip()] if args.files
               else changed_files(args.base))
    selected, unmatched = plan(changed, test_files())

    print(f"changed: {len(changed)} file(s) vs {args.base}")
    for name in changed:
        print(f"  - {name}")
    print(f"selected: {len(selected)} test file(s)")
    for test, tokens in sorted(selected.items()):
        print(f"  + {test.relative_to(ROOT)}  <- {', '.join(tokens)}")
    for test, reasons in sorted(selected.items()):
        if "changed itself" in reasons:
            print(f"  * {test.relative_to(ROOT)}  <- changed itself")
    if unmatched:
        # Loud on purpose: a code change nobody tests is a fact the merger needs, not a
        # detail to average away with the ones that matched.
        print(f"unmatched: {len(unmatched)} changed file(s) no test mentions")
        for name in unmatched:
            print(f"  ? {name}")
        print("  -> run --full before merging something this lane cannot see")
    if not selected:
        print("nothing selected; run --full")
        return 0
    if args.list:
        return 0
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               *[str(t) for t in sorted(selected)]]
    return _run(command, args.max_seconds)


def _run(command: list[str], max_seconds: int) -> int:
    base = pathlib.Path(tempfile.mkdtemp(prefix="hg-test-lane-"))   # outside the platform
    command += [f"--basetemp={base}"]
    print("run: " + " ".join(command))
    try:
        return subprocess.run(command, cwd=ROOT, timeout=max_seconds).returncode
    except subprocess.TimeoutExpired:
        print(f"lane exceeded {max_seconds}s: selection kept, nothing concluded. "
              f"Run a narrower file list or raise --max-seconds.", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
