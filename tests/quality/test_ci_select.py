"""The CI selector must be conservative, and the guard set must always be in it.

A selection tool that under-selects is worse than no selection at all: the green check then
means "the tests I chose passed". So the properties here are the safety ones — the guards are
always present, an unmapped or core path falls back to the full suite, and a documentation-only
change really does select nothing outside the guards — plus one positive case per artefact
kind, so a change to an adapter or a harness does pull in its own tests.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import ci_select  # noqa: E402


def test_the_guard_set_exists_and_is_always_selected() -> None:
    for name in ci_select.GUARDS:
        assert (ci_select.TESTS / name).is_file(), f"guard {name} does not exist"
    picked, _ = ci_select.select(["docs/leaderboards.md"])
    assert set(ci_select.GUARDS) <= set(picked)


def test_a_documentation_change_selects_only_the_guards() -> None:
    picked, reasons = ci_select.select(["README.md", "docs/leaderboards.md", ".github/x.yml"])
    assert set(picked) == set(ci_select.GUARDS), picked
    assert all("documentation" in reason for reason in reasons), reasons


def test_an_adapter_change_pulls_in_that_dataset_and_the_contract_tests() -> None:
    picked, _ = ci_select.select(["data/datasets/bbeh.py"])
    assert "test_bbeh_duplicate_policy.py" in picked
    assert "test_datasets_doc.py" in picked
    assert set(ci_select.GUARDS) <= set(picked)


def test_a_harness_or_method_change_pulls_in_its_own_tests() -> None:
    harness, _ = ci_select.select(["base_harness/loop/agent.py"])
    assert any(name.startswith("test_harness_") for name in harness), harness
    method, _ = ci_select.select(["methods/dgm/run.py"])
    assert any(name.startswith(("test_dgm_", "test_method_")) for name in method), method


def test_a_tool_change_pulls_in_its_own_test() -> None:
    picked, _ = ci_select.select(["tools/contributors.py"])
    assert "test_contributors.py" in picked
    assert set(ci_select.GUARDS) <= set(picked)


def test_a_core_path_falls_back_to_the_full_suite() -> None:
    for path in ("driver.py", "eval/run/runner.py", "harnessgrad/readiness.py"):
        picked, reasons = ci_select.select([path])
        assert picked == [], (path, picked)
        assert any("core path" in reason for reason in reasons), (path, reasons)


def test_an_unmapped_path_falls_back_to_the_full_suite() -> None:
    picked, reasons = ci_select.select(["site-redirect/index.html"])
    assert picked == [], picked
    assert any("not mapped" in reason for reason in reasons), reasons


def test_a_wide_change_falls_back_to_the_full_suite() -> None:
    picked, reasons = ci_select.select([f"docs/note-{index}.md"
                                        for index in range(ci_select.WIDE + 1)])
    assert picked == [], picked
    assert any("running everything" in reason for reason in reasons), reasons


def test_a_change_to_a_test_runs_that_test() -> None:
    picked, _ = ci_select.select(["tests/quality/test_contributors.py"])
    assert "test_contributors.py" in picked
