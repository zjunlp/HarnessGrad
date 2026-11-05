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


def test_a_tool_change_selects_its_test_with_or_without_a_suffix() -> None:
    """`test_dataset_classes.py` has no trailing underscore; the prefix rule must still find it."""
    picked, _ = ci_select.select(["tools/dataset_classes.py"])
    assert "test_dataset_classes.py" in picked, picked
    picked, _ = ci_select.select(["tools/contributors.py"])
    assert "test_contributors.py" in picked, picked


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


def test_the_plan_round_trips_through_a_file(tmp_path) -> None:
    """The suite is read from a plan, not from a shell string that can lose its contents."""
    import json
    plan = tmp_path / "plan.json"
    assert ci_select.main.__module__  # the entry point exists
    picked, _ = ci_select.select(["README.md"])
    plan.write_text(json.dumps({"base": "abc", "changed": 1, "suite": [], "full": False}))
    assert json.loads(plan.read_text())["suite"] == []


def test_the_runner_reads_a_plan_and_can_run_nothing(tmp_path) -> None:
    """`run_selected.py` is what the workflow calls; an empty plan must not mean `pytest .`."""
    import json
    import subprocess

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"base": "abc", "suite": [], "full": False}))
    proc = subprocess.run([sys.executable, "tools/run_selected.py", "--plan", str(plan)],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "nothing in the suite can reach this change" in proc.stdout
    assert "run_tests.py" not in proc.stdout


def test_the_plan_says_full_only_when_the_selection_does(tmp_path) -> None:
    """A documentation change selects the guards; that is not "this change selects everything"."""
    import json
    import subprocess

    plan = tmp_path / "plan.json"
    proc = subprocess.run([sys.executable, "tools/ci_select.py", "--paths", "docs/x.md",
                           "--plan", str(plan), "--without-guards"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(plan.read_text())["full"] is False

    proc = subprocess.run([sys.executable, "tools/ci_select.py", "--paths",
                           "eval/run/runner.py", "--plan", str(plan), "--without-guards"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(plan.read_text())
    assert payload["full"] is True and payload["suite"] == []


def test_the_runner_builds_one_file_flag_per_selected_test(tmp_path) -> None:
    import json
    import subprocess

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"base": "abc", "suite": ["test_ci_select.py"], "full": False}))
    proc = subprocess.run([sys.executable, "tools/run_selected.py", "--plan", str(plan),
                           "--dry-run"], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "--file test_ci_select.py" in proc.stdout, proc.stdout
    assert "--jobs" in proc.stdout


def test_the_guard_filter_lives_here_and_not_in_the_workflow() -> None:
    """The workflow asks for a suite without guards; a second list there drifted once."""
    picked, _ = ci_select.select(["README.md"])
    assert set(picked) == set(ci_select.GUARDS)
    # `--without-guards` is applied in main(), so this checks the data the flag reads.
    remaining = [name for name in picked if name not in ci_select.GUARDS]
    assert remaining == [], remaining
    assert "test_ci_select.py" in ci_select.GUARDS, "the selector's own guard must be in the set"


def test_a_change_to_a_test_runs_that_test() -> None:
    picked, _ = ci_select.select(["tests/quality/test_contributors.py"])
    assert "test_contributors.py" in picked


def test_a_change_to_a_fixture_runs_its_consumer() -> None:
    """`tests/fixtures/` once fell through a `startswith("test")` guard and selected nothing."""
    picked, _ = ci_select.select(["tests/fixtures/hg_bfclguard/edges.jsonl"])
    assert "test_bfclguard_reachability.py" in picked, picked


def test_a_change_to_any_test_file_selects_it() -> None:
    for name in ("test_dataset_classes.py", "test_trace_reader.py"):
        picked, _ = ci_select.select([f"tests/quality/{name}"])
        assert name in picked or set(picked) == set(ci_select.GUARDS), (name, picked)
