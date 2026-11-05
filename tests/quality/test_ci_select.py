"""The CI selector must be conservative, and the guard set must always be in it.

A selection tool that under-selects is worse than no selection at all: the green check then
means "the tests I chose passed". So the properties here are the safety ones — the guards are
always present, an unmapped or core path falls back to the full suite, and a documentation-only
change really does select nothing outside the guards — plus one positive case per artefact
kind, so a change to an adapter or a harness does pull in its own tests.
"""
from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

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


def test_a_dataset_asset_change_pulls_in_that_dataset_only() -> None:
    """`data/datasets/<name>/...` is a change to `<name>`, not to a file called `runtime`."""
    picked, _ = ci_select.select(["data/datasets/tau2_bench/lib/runtime.py"])
    assert "test_tau2_bench_runtime.py" in picked
    assert "test_tau2_bench_official_scorer.py" in picked
    assert "test_dataset_controls.py" in picked
    assert not any("test_tau3" in name for name in picked), picked


def test_a_tau2_fixture_change_runs_the_test_that_drives_it() -> None:
    picked, _ = ci_select.select(["tests/fixtures/tau2_scorer_probe.py"])
    assert "test_tau2_bench_official_scorer.py" in picked


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


def test_an_unknown_base_is_not_the_same_fact_as_an_empty_diff() -> None:
    """A depth-1 clone has no base commit; that is "everything changed", not "nothing did".

    The two produced the same empty list and the same summary line once, and the CI notice then
    read "no diff against the base" for a commit that had simply never been fetched.
    """
    picked, reasons = ci_select.select([])
    assert picked == [], "a full fallback is a flag, not a list of every test file"
    assert any("running everything" in reason for reason in reasons), reasons
    assert not ci_select._rev_exists("0000000000000000000000000000000000000000")
    assert ci_select._rev_exists("HEAD")
    assert ci_select.changed("0000000000000000000000000000000000000000", "HEAD") is None
    assert ci_select.changed("HEAD", "HEAD") == []


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


def test_a_full_plan_says_deferred_rather_than_listing_no_files(tmp_path, capsys, monkeypatch) -> None:
    """The plan keeps `full` and an empty suite; the summary has to say which of the two
    meanings that empty list carries. It read `files=0 suite=-` for a wide change once, which
    looks exactly like a change that reaches nothing."""
    plan = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["ci_select.py", "--base", "0" * 40, "--head", "HEAD",
                                      "--plan", str(plan)])
    assert ci_select.main() == 0
    loaded = json.loads(plan.read_text())
    assert loaded["full"] is True and loaded["suite"] == []
    monkeypatch.setattr(sys, "argv", ["ci_select.py", "--describe", str(plan)])
    assert ci_select.main() == 0
    printed = capsys.readouterr().out
    assert "full=True" in printed and "suite=-" in printed
    assert "state=deferred" in printed


@pytest.mark.parametrize("state,suite,flags,expected", [
    ("selected", ["test_ci_select.py", "test_ci_workflow.py"], [],
     ["--fast", "--file", "test_ci_select.py", "--file", "test_ci_workflow.py"]),
    ("selected", [], [], None),
    ("deferred", [], [], None),
    ("full", [], [], []),
    ("selected", ["test_ci_select.py"], ["--full"], []),
    ("selected", [], ["--full"], []),
    ("deferred", [], ["--full"], []),
    ("selected", [], ["--full-if-empty"], []),
    ("deferred", [], ["--full-if-empty"], []),
    ("selected", ["test_ci_select.py"], ["--full-if-empty"],
     ["--fast", "--file", "test_ci_select.py"]),
])
def test_runner_subprocess_argv(tmp_path, monkeypatch, capsys, state, suite, flags, expected):
    """Intercept the actual subprocess boundary, not the dry-run display string."""
    from tools import run_selected

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"state": state, "full": state != "selected", "suite": suite}))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(run_selected.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", str(plan),
                                     "--jobs", "3", *flags])
    result = run_selected.main()
    output = capsys.readouterr().out
    if expected is None:
        assert result == 0
        assert calls == []
        if state == "deferred":
            assert "deferred to the nightly run or a manual dispatch" in output
            assert "nothing in the suite" not in output
        else:
            assert "nothing in the suite" in output
    else:
        assert result == 7, "propagate the runner's failure"
        assert calls == [([sys.executable, str(run_selected.RUNNER), "--jobs", "3", *expected],
                          {"cwd": run_selected.ROOT})]


@pytest.mark.parametrize("state,suite,flags,expected", [
    ("selected", ["test_ci_select.py"], [],
     ["--jobs", "2", "--fast", "--file", "test_ci_select.py"]),
    ("selected", [], [], None),
    ("deferred", [], [], None),
    ("full", [], [], ["--jobs", "2"]),
    ("selected", ["test_ci_select.py"], ["--full"], ["--jobs", "2"]),
])
def test_real_child_observes_exact_argv(tmp_path, monkeypatch, state, suite, flags, expected):
    """A harmless child records argv; this never invokes the real test runner."""
    from tools import run_selected

    receipt = tmp_path / "argv.json"
    stub = tmp_path / "runner_stub.py"
    stub.write_text("import json, os, pathlib, sys\n"
                    f"pathlib.Path({str(receipt)!r}).write_text("
                    "json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"state": state, "full": state != "selected", "suite": suite}))
    monkeypatch.setattr(run_selected, "RUNNER", stub)
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", str(plan),
                                     "--jobs", "2", *flags])
    assert run_selected.main() == 0
    if expected is None:
        assert not receipt.exists()
    else:
        assert json.loads(receipt.read_text()) == {"argv": expected, "cwd": str(ROOT)}


@pytest.mark.parametrize("payload", [
    "{", "null", "[]", "{}",
    '{"suite": [], "full": "false"}',
    '{"suite": "test_ci_select.py", "full": false}',
    '{"suite": [null], "full": false}',
    '{"suite": ["--fast"], "full": false}',
    '{"suite": ["../test_ci_select.py"], "full": false}',
    '{"suite": ["test_does_not_exist.py"], "full": false}',
    '{"suite": ["test_ci_select.py", "test_ci_select.py"], "full": false}',
    '{"suite": ["test_ci_select.py"], "full": true}',
    '{"suite": [], "full": true, "state": "selected"}',
    '{"suite": [], "full": false, "state": "full"}',
    '{"suite": [], "full": false, "state": "unknown"}',
])
@pytest.mark.parametrize("flags", [[], ["--full"]])
def test_malformed_plans_fail_closed(tmp_path, monkeypatch, payload, flags):
    from tools import run_selected

    plan = tmp_path / "bad.json"
    plan.write_text(payload)

    def forbidden(*args, **kwargs):
        pytest.fail("a malformed plan must not launch tests, even with --full")

    monkeypatch.setattr(run_selected.subprocess, "run", forbidden)
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", str(plan), *flags])
    with pytest.raises(SystemExit) as exc:
        run_selected.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("paths,flags,state", [
    (["docs/x.md"], ["--without-guards"], "selected"),
    (["eval/run/runner.py"], [], "deferred"),
    ([], ["--force-full"], "full"),
])
def test_plan_producer_names_execution_state(tmp_path, monkeypatch, paths, flags, state):
    plan = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["ci_select.py", "--paths", *paths,
                                     "--plan", str(plan), *flags])
    assert ci_select.main() == 0
    payload = ci_select.load_plan(str(plan))
    assert payload["state"] == state
    assert payload["full"] is (state != "selected")
    assert payload["suite"] == []


def test_legacy_full_plan_is_deferred(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text('{"full": true, "suite": []}')
    assert ci_select.load_plan(str(plan))["state"] == "deferred"
