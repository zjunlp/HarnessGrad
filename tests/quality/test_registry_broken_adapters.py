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


def test_every_declared_adapter_is_published_or_named_as_broken():
    """The catalogue is complete or it says what it dropped -- on any machine.

    This used to assert that nothing was broken and that 59 were published, which is true on
    a provisioned checkout and false on a fresh one: two adapters read an optional module
    (`numpy`, `chess`) at import, so a bare interpreter publishes 57 and the assertion failed
    on exactly the machine a stranger starts from. The property is the one the module is
    about: nothing disappears *silently*. A declared adapter is either published, or it is
    named with its reason.
    """
    declared = {path.stem for path in (ROOT / "data/datasets").glob("*.py")
                if path.stem != "__init__"}
    published = set(registry.published())
    broken = registry.broken_adapters()
    assert published <= declared, f"published names with no declaration: {published - declared}"
    assert set(broken) <= declared, f"broken names with no declaration: {set(broken) - declared}"
    assert published | set(broken) == declared, (
        "declared adapters that are neither published nor named as broken: "
        f"{sorted(declared - published - set(broken))}")
    assert not (published & set(broken)), "an adapter is both published and reported broken"
    print(f"published {len(published)} of {len(declared)} declared, {len(broken)} broken here")
