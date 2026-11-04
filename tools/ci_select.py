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
    """Test files whose name starts with one of `prefixes`.

    A prefix that ends in `_` matches a family (`test_dataset_*`); one that does not is an
    exact file name, which is how `tools/dataset_classes.py` finds `test_dataset_classes.py`
    -- a stem-matched test with no trailing underscore is a family of one.
    """
    picked = []
    for path in sorted(TESTS.glob("test_*.py")):
        for prefix in prefixes:
            if path.name == prefix or path.name.startswith(prefix):
                picked.append(path.name)
                break
    return picked


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
        if any(path == core or path.startswith(core) for core in CORE):
            return [], [f"{path} is a core path: running everything"]
        if path.startswith(("docs/", ".github/")) or path.endswith(".md"):
            reasons.append(f"{path}: documentation only")
        elif path.startswith("data/datasets/"):
            # The dataset name, not the file name: an adapter's assets live in a directory of
            # the same name (`data/datasets/tau2_bench/lib/runtime.py` is a change to
            # `tau2_bench`), and keying on the file name would select `test_runtime_*`.
            relative = path[len("data/datasets/"):]
            wanted = _matching((f"test_{_stem(relative.split('/')[0])}_",
                                f"test_{_stem(relative.split('/')[0])}.py",
                                "test_dataset_", "test_datasets_doc"))
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
            wanted = _matching((f"test_{stem}.py", f"test_{stem}_"))
            picked.update(wanted)
            reasons.append(f"{path}: {len(wanted)} tool tests")
        elif path.startswith("tests/quality/"):
            if (TESTS / Path(path).name).is_file():
                picked.add(Path(path).name)
            reasons.append(f"{path}: itself")
        elif path.startswith("tests/fixtures/"):
            # A fixture is only evidence through the test that drives it. `tau2_scorer_probe.py`
            # is run by the official-scorer test because the adapter needs a 3.13 interpreter the
            # test process does not have; `hg_bfclguard/` is the reachability fixture.
            if path.startswith("tests/fixtures/tau2_"):
                picked.add("test_tau2_bench_official_scorer.py")
            else:
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
    parser.add_argument("--describe", default="",
                        help="print a plan's selection and record it on the run page")
    parser.add_argument("--plan", default="",
                        help="write the selection as JSON for tools/run_selected.py")
    parser.add_argument("--without-guards", action="store_true",
                        help="drop the guard files, for a job whose previous stage already ran "
                             "them on this commit")
    parser.add_argument("--force-full", action="store_true")
    args = parser.parse_args()

    if args.describe:
        import json
        import os
        plan = json.loads(Path(args.describe).read_text(encoding="utf-8"))
        suite = plan.get("suite") or []
        summary = [f"base={plan.get('base')} full={plan.get('full')} "
                   f"changed={plan.get('changed')} files={len(suite)}"]
        summary += [f"  {name}" for name in suite]
        print("\n".join(summary))
        # Written here rather than in the workflow: `$GITHUB_STEP_SUMMARY` and the annotation
        # syntax are the two places a shell step gets wrong, and this is the third iteration.
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a", encoding="utf-8") as handle:
                handle.write("### Suite for this change\n\n")
                handle.write(f"base `{plan.get('base')}` · changed {plan.get('changed')} "
                             f"file(s)\n\n")
                if suite:
                    for name in suite:
                        handle.write(f"- `{name}`\n")
                else:
                    handle.write("_nothing outside the guards; they ran in the checks job_\n")
        print(f"::notice::base={plan.get('base')} files={len(suite)} "
              f"suite={','.join(suite) or '-'}")
        return 0

    paths = args.paths if args.paths is not None else (
        changed(args.base, args.head) if args.base else [])
    picked, reasons = ([], ["full run requested"]) if args.force_full else select(paths)

    # `full` is a property of the *selection*, not of what is left after the guards are
    # subtracted: a documentation change selects the guards alone, which leaves an empty suite
    # and is not the same thing as "this change selects everything".
    full = not picked and any("running everything" in reason for reason in reasons)
    if args.without_guards:
        picked = [name for name in picked if name not in GUARDS]
    if args.plan:
        import json
        Path(args.plan).write_text(json.dumps(
            {"base": args.base or None, "changed": len(paths), "suite": picked, "full": full},
            indent=1) + "\n", encoding="utf-8")
        return 0
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
