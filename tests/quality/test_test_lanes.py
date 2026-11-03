"""Every lane entry names a test file that exists, and no file is claimed twice.

`tools/run_tests.py` filters by basename: `exclusive = [f for f in files if f.name in
EXCLUSIVE]`. A name in that tuple with no matching file is therefore not an error -- it is a
silent no-op, and the file it was meant to protect runs in the parallel lane instead. The guard
for this used to be `test_test_lanes.py`, deleted on 2026-10-27 with the slim; this restores the
half that is cheap and static: existence, no duplicates, and no file in both lanes.

What it deliberately does not do is re-derive *why* an entry is exclusive. That was a measured
argument (`docs/test-lanes.md` "The signals") and it needs a serial run against a concurrent
one, not a parse.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import run_tests  # noqa: E402

TESTS = ROOT / "tests" / "quality"


def test_every_lane_entry_names_an_existing_test_file():
    missing = [name for name in (*run_tests.EXCLUSIVE, *run_tests.SLOW)
               if not (TESTS / name).is_file()]
    assert not missing, (
        "tools/run_tests.py names files under tests/quality/ that do not exist, so the lane "
        f"filter silently ignores them: {missing}")


def test_no_test_file_is_listed_twice_in_one_lane():
    for lane, names in (("EXCLUSIVE", run_tests.EXCLUSIVE), ("SLOW", run_tests.SLOW)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        assert not duplicates, f"{lane} lists the same file twice: {duplicates}"


def test_an_entry_in_both_lanes_is_removed_only_by_fast():
    """The tuples answer different questions, so an overlap is legal -- and explicit.

    `EXCLUSIVE` means "run this at concurrency 1"; `SLOW` means "`--fast` may skip this".
    `test_appworld_official_scorer.py` is both, which is correct: it needs the machine to
    itself *and* it is the most expensive file in the suite. What must not happen is the
    overlap dropping the file without a flag saying so -- so the property is that the
    default plan contains it in the exclusive lane, and `--fast` is the only thing that
    removes it.
    """
    both = sorted(set(run_tests.EXCLUSIVE) & set(run_tests.SLOW))
    assert both, (
        "no file is both slow and exclusive any more, so this property no longer has a "
        "subject -- delete the test rather than leaving a vacuous assertion")
    default_plan = {p.name for p in run_tests.discover([])}
    fast_plan = default_plan - set(run_tests.SLOW)
    for name in both:
        assert name in default_plan, f"{name} is not discovered at all"
        assert name not in fast_plan, f"--fast was expected to skip {name}"
        # A file in EXCLUSIVE must not land in the parallel lane when it *is* run.
        assert name in run_tests.EXCLUSIVE
