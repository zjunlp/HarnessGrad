"""A catalogue that drops an adapter must say which one and why.

`registry.published()` imports every adapter and omits the ones that raise, because a name a
reader cannot load is not a dataset they can pick. Dropping silently is the problem: on a fresh
clone without the optional `chess`/`numpy`, or in an install that did not ship an adapter's
asset, the catalogue is simply shorter and nothing says so. `broken_adapters()` keeps the
reason, and `tools/datasets.py --list` prints it.

Measured 2026-11-03 in a venv with no extras: a wheel install published 54 of 59, dropping
`chessbench_puzzles` (`No module named 'chess'`), `livecodebench` (`numpy`), `strongreject` and
`tau_bench` (assets the wheel did not ship) and `authored_example_fix_bug` (excluded by
`pyproject.toml`), with no diagnostic anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry  # noqa: E402


def test_a_broken_adapter_is_dropped_from_published_and_named():
    """Falsification: an adapter that raises ImportError is absent *and* reported."""
    original = registry.kind_of

    def kind_of(name):
        if name == "gsm8k":
            raise ImportError("No module named 'chess'")
        return original(name)

    registry.kind_of = kind_of
    try:
        names = registry.published()
        broken = registry.broken_adapters()
    finally:
        registry.kind_of = original

    assert "gsm8k" not in names, "a name that cannot import must not be offered"
    assert broken.get("gsm8k", "").startswith("ImportError"), broken
    assert "chess" in broken["gsm8k"], broken["gsm8k"]
    # The rest of the catalogue is unaffected, so this is one row, not an empty list.
    assert "agentdojo" in names, names


def test_the_host_this_runs_on_has_no_unimportable_adapter():
    """On a provisioned checkout every published adapter imports.

    A failure here is information, not necessarily a bug: it names the missing module or
    asset. It is asserted rather than skipped because a silently short catalogue is exactly
    the defect the rest of this module is about.
    """
    published = registry.published()
    broken = registry.broken_adapters()
    assert not broken, (
        "these adapters cannot be imported on this machine, so the catalogue is missing them: "
        + "; ".join(f"{name} ({reason})" for name, reason in sorted(broken.items())))
    assert len(published) == 59, f"expected 59 published datasets, got {len(published)}"
