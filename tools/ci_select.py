#!/usr/bin/env python3
"""Pick the tests a change can break, so CI does not run the whole suite for a README edit.

Why a selection and not a bigger runner
---------------------------------------
The suite is ~750 s under `--fast` on two workers, and almost all of that is real work:
subprocess launches, sandbox namespaces, container probes. Spending it on an edit that touched
one adapter is the reason the gate feels slow. A bigger runner buys minutes; running the tests
that the change can actually reach buys the rest of the hour.

The rule is deliberately conservative and two-sided
---------------------------------------------------
*Anything* that could plausibly be affected is selected, and anything the tool cannot reason
about falls back to the full suite. A missed test is a bug in this file; a redundant test is
one minute. The mapping is therefore mechanical rather than clever:

    data/datasets/<x>.py      -> test_<x>_*, test_dataset_*, test_datasets_doc
    base_harness/<x>/**       -> test_<x>_*, test_harness_*
    methods/<x>/**            -> test_<x>_*, test_method_*
    tools/<x>.py              -> test_<x>_*
    tests/quality/<name>.py   -> itself, plus the guards
    docs/**, *.md             -> the guards only
    .github/**                -> the guards only
    driver.py, eval/**, harnessgrad/**  -> full suite (core paths)
    anything else             -> full suite

The guard set always runs: it is a few seconds and it is what catches documentation,
inventory and workflow drift.

Usage
-----
    python3 tools/ci_select.py --base origin/main --head HEAD          # human summary
    python3 tools/ci_select.py --base ... --head ... --files           # one path per line
    python3 tools/ci_select.py --paths a/b.py c.md --files
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests" / "quality"

#: The gate that runs on every change, whatever it touched.
GUARDS = (
    "test_ci_select.py",
    "test_ci_workflow.py",
    "test_contributors.py",
    "test_documentation_commands.py",
    "test_documentation_links.py",
    "test_import_direction.py",
    "test_registry_broken_adapters.py",
    "test_test_lanes.py",
)

#: Paths whose blast radius is the whole platform.
CORE = ("driver.py", "eval/", "harnessgrad/", "conftest.py", "pyproject.toml", "install.sh")

#: A change that touches more than this many files is not worth reasoning about.
WIDE = 25

#: Above this many selected files, running everything is simpler than keeping the list honest.
MAX_SELECTION = 60


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout


def changed(base: str, head: str) -> list[str]:
    out = run(["git", "diff", "--name-only", base, head])
    if not out.strip():
        # No merge base (a shallow clone, a force-push): treat it as "everything changed".
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _matching(prefixes: tuple[str, ...]) -> list[str]:
    return sorted(path.name for path in TESTS.glob("test_*.py")
                  if any(path.name.startswith(prefix) for prefix in prefixes))


def _stem(name: str) -> str:
    return name[:-3] if name.endswith(".py") else name


def select(paths: list[str]) -> tuple[list[str], list[str]]:
    """Return (selected test files, reasons). An empty selection means "run everything"."""
    if not paths:
        return [], ["no diff against the base: running everything"]
    if len(paths) > WIDE:
        return [], [f"{len(paths)} files changed: running everything"]
    reasons: list[str] = []
    picked: set[str] = set(GUARDS)

    for path in paths:
        stem = _stem(Path(path).name)
        if path.startswith("test"):
            continue
        if any(path == core or path.startswith(core) for core in CORE):
            return [], [f"{path} is a core path: running everything"]
        if path.startswith(("docs/", ".github/")) or path.endswith(".md"):
            reasons.append(f"{path}: documentation only")
        elif path.startswith("data/datasets/"):
            wanted = _matching((f"test_{stem}_", "test_dataset_", "test_datasets_doc"))
            picked.update(wanted)
            reasons.append(f"{path}: {len(wanted)} dataset tests")
        elif path.startswith("base_harness/"):
            wanted = _matching((f"test_{stem}_", "test_harness_"))
            picked.update(wanted)
            reasons.append(f"{path}: {len(wanted)} harness tests")
        elif path.startswith("methods/"):
            wanted = _matching((f"test_{stem}_", "test_method_"))
            picked.update(wanted)
            reasons.append(f"{path}: {len(wanted)} method tests")
        elif path.startswith("tools/"):
            wanted = _matching((f"test_{stem}_",))
            picked.update(wanted)
            reasons.append(f"{path}: {len(wanted)} tool tests")
        elif path.startswith("tests/quality/"):
            if (TESTS / Path(path).name).is_file():
                picked.add(Path(path).name)
            reasons.append(f"{path}: itself")
        elif path.startswith("tests/fixtures/"):
            picked.update({"test_bfclguard_reachability.py"})
            reasons.append(f"{path}: fixture consumer")
        else:
            return [], [f"{path} is not mapped: running everything"]

    if len(picked) > MAX_SELECTION:
        return [], [f"{len(picked)} files selected: running everything"]
    return sorted(picked), reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="", help="the commit to compare against")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--paths", nargs="*", default=None,
                        help="explicit paths instead of a git diff")
    parser.add_argument("--files", action="store_true", help="print only the file names")
    parser.add_argument("--force-full", action="store_true")
    args = parser.parse_args()

    paths = args.paths if args.paths is not None else (
        changed(args.base, args.head) if args.base else [])
    picked, reasons = ([], ["full run requested"]) if args.force_full else select(paths)

    if args.files:
        print("\n".join(picked))
        return 0

    print(f"changed: {len(paths)} file(s)")
    for reason in reasons[:12]:
        print(f"  - {reason}")
    if not picked:
        print("selection: the full suite")
    else:
        print(f"selection: {len(picked)} file(s), guards included")
        for name in picked:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
