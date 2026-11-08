"""Selected-only planning controls: authored declarations, no actors or models."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from harnessgrad.orchestration import selected_dataset as selected


class Source:
    def __init__(self):
        self.events, self.active = [], False
        self.tasks = [{"task_id": name, "goal": "authored " + name} for name in ("a", "b", "exam")]
        self.scorable = {task["task_id"]: "authored answer" for task in self.tasks}
        self.split = {"train": ["b", "a"], "eval": ["exam"]}
        self.failure = None

    @contextmanager
    def session(self):
        self.active = True
        self.events.append("enter")
        try:
            yield self
        finally:
            self.events.append("exit")
            self.active = False

    def _read(self, stage, ids=None):
        assert self.active, "the caller must own the provider session"
        self.events.append((stage, ids))
        if self.failure and self.failure[0] == stage:
            raise self.failure[1]

    def load_split(self, name):
        self._read("catalog")
        return self.tasks, self.scorable, self.split

    def oracle_source(self, name):
        self._read("oracle")
        return "authored", {"purpose": "control"}

    def load_tasks(self, name, *, task_ids):
        self._read("declarations", list(task_ids))
        assert "exam" not in task_ids
        return [], {tid: {} for tid in task_ids}, {tid: {"kind": "answer"} for tid in task_ids}

    def load_envs(self, name, *, task_ids):
        self._read("envs", list(task_ids))
        return {tid: {"kind": "files"} for tid in task_ids}


class Reporter:
    def __init__(self):
        self.events = []

    def note(self, message, *, level):
        self.events.append((message, level))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    harness = tmp_path / "source"
    harness.mkdir()
    (harness / "harness.json").write_text(json.dumps({"env_kinds": ["files"]}))
    options = selected.SelectionOptions("demo", "declared", "train", None, "all", None, None, True, False)
    return options, Source(), harness, Reporter()


def build(setup, **overrides):
    options, source, harness, reporter = setup
    with source.session():
        return selected.build_selected_run_plan(options, source, runs_root=harness.parent / "runs",
            scenario=None, model={"backend": "mock", "live": False}, harness_source=harness,
            policy_evidence=None, reporter=reporter, **overrides)


def test_plan_reads_only_selected_declarations_and_has_no_process_state_writes(setup):
    options, source, _, reporter = setup
    before_options, before_env = asdict(options), dict(os.environ)
    plan = build(setup)
    assert source.events == ["enter", ("catalog", None), ("oracle", None),
        ("declarations", ["b", "a"]), ("envs", ["b", "a"]), "exit"]
    assert plan.selection.ids == ["b", "a"]
    assert [task["task_id"] for task in plan.selection.tasks] == ["a", "b"]
    assert set(plan.setups) == set(plan.verifiers) == set(plan.envs) == {"a", "b"}
    assert plan.partition.scorable is source.scorable
    assert plan.partition.oracle_source == "authored"
    assert plan.scoring_identity is plan.dataset_runtime_identity is None
    assert plan.verification_context["sha256"]
    assert asdict(options) == before_options and dict(os.environ) == before_env
    assert not reporter.events
    with pytest.raises(FrozenInstanceError):
        options.side = "eval"


@pytest.mark.parametrize("sampling,expected", [("diagnostic", 1), ("random_1", 1), ("below_0.5", 2), ("above_0.5", 2)])
def test_early_sampling_keeps_eligible_roster_and_defers_score_based_selection(setup, sampling, expected):
    options, source, harness, reporter = setup
    plan = build((replace(options, sampling=sampling), source, harness, reporter))
    assert len(plan.selection.ids) == expected
    assert plan.selection.eligible_ids == ["b", "a"]
    assert source.events[3] == ("declarations", plan.selection.ids)


@pytest.mark.parametrize("scenario", [None, {"name": "authored"}])
@pytest.mark.parametrize("stage", ["catalog", "oracle", "declarations", "envs"])
def test_loader_exception_semantics_are_preserved(setup, stage, scenario):
    options, source, harness, reporter = setup
    error = ImportError("authored dependency absent")
    source.failure = (stage, error)
    with source.session():
        if scenario is None:
            with pytest.raises(ImportError) as caught:
                selected.build_selected_run_plan(options, source, runs_root=harness.parent,
                    scenario=scenario, model={}, harness_source=harness, policy_evidence=None, reporter=reporter)
            assert caught.value is error
        else:
            with pytest.raises(selected.SelectedRunRefused) as caught:
                selected.build_selected_run_plan(options, source, runs_root=harness.parent,
                    scenario=scenario, model={}, harness_source=harness, policy_evidence=None, reporter=reporter)
            assert str(caught.value).startswith("refusing --scenario: dataset 'demo'")
            assert caught.value.__cause__ is error
    assert source.events[-1] == "exit"


def test_cancellation_is_not_turned_into_a_refusal_and_closes_the_callers_session(setup):
    options, source, harness, reporter = setup
    error = KeyboardInterrupt("authored cancellation")
    source.failure = ("declarations", error)
    with pytest.raises(KeyboardInterrupt) as caught:
        build(setup)
    assert caught.value is error and source.events[-1] == "exit"
    assert not source.active and not reporter.events


def test_capability_refusal_precedes_image_resolution_and_context_capture(setup, monkeypatch):
    options, source, harness, reporter = setup
    source.load_envs = lambda *a, **kw: {tid: {"kind": "exec", "image": "authored:tag"} for tid in kw["task_ids"]}
    monkeypatch.setattr(selected, "_resolve_envs", lambda *a: pytest.fail("image lookup preceded capability refusal"))
    monkeypatch.setattr(selected.verification_context, "capture", lambda *a, **kw: pytest.fail("captured refused context"))
    with pytest.raises(selected.SelectedRunRefused, match="refusing to run") as caught:
        build(setup)
    assert caught.value.channel == "console"


def test_admission_order_uses_selected_declarations_and_then_resolved_environment(setup, monkeypatch):
    events = []
    monkeypatch.setattr(selected.readiness, "selected_model_checks", lambda *a, **k: events.append("models") or [])
    monkeypatch.setattr(selected.scoring_identity, "resolve", lambda *a: events.append("scoring") or {"authored": 1})
    monkeypatch.setattr(selected.dataset_runtime_identity, "capture", lambda *a: events.append("runtime") or {"authored": 2})
    real_manifest = selected.manifest
    monkeypatch.setattr(selected, "manifest", lambda path: events.append("manifest") or real_manifest(path))
    resolved = {"a": {"kind": "files", "authored-resolved": True}}
    monkeypatch.setattr(selected, "_resolve_envs", lambda envs: events.append("images") or (resolved, None))
    def capture(tasks, scorable, setups, verifiers, envs, *, sandbox):
        assert envs is resolved and sandbox
        events.append("context")
        return {"authored": 3}
    monkeypatch.setattr(selected.verification_context, "capture", capture)
    plan = build(setup)
    assert events == ["models", "scoring", "runtime", "manifest", "images", "context"]
    assert plan.envs is resolved


def test_selected_model_refusal_preserves_stdout_then_stderr(setup, monkeypatch, capsys):
    check = SimpleNamespace(required=True, ok=False)
    monkeypatch.setattr(selected.readiness, "selected_model_checks", lambda *a, **kw: [check])
    monkeypatch.setattr(selected.readiness, "render", lambda checks: "authored readiness table")
    monkeypatch.setattr(selected.scoring_identity, "resolve", lambda *a: pytest.fail("identity before model admission"))
    with pytest.raises(selected.SelectedRunRefused) as caught:
        build(setup)
    caught.value.report(setup[3])
    output = capsys.readouterr()
    assert output.out == "authored readiness table\n"
    assert output.err == "refusing to run: selected judge/environment configuration failed before H0\n"


@pytest.fixture
def driver_setup(tmp_path, monkeypatch):
    import driver
    from harnessgrad import config
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    config.reset()
    monkeypatch.setattr(driver, "stage_workspace", lambda *a, **kw: pytest.fail("planning staged a workspace"))
    monkeypatch.setattr(driver, "evaluate", lambda *a, **kw: pytest.fail("planning ran an actor"))
    argv = ["driver.py", "--harness", "loop_plain", "--dataset", "demo", "--sampling", "all",
            "--rounds", "0", "--trials", "1", "--jobs", "1", "--tasks", "t01", "--improver", "none",
            "--no-sandbox", "--run-id", "d1-control", "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / "work")]
    yield driver, argv
    config.reset()


def test_driver_holds_provider_session_until_selected_admission_before_claim(driver_setup, monkeypatch):
    import sys
    driver, argv = driver_setup
    events = []
    real_session = driver.datasets.provider_session
    @contextmanager
    def held_session():
        events.append("enter")
        with real_session():
            try:
                yield
            finally:
                events.append("exit")
    monkeypatch.setattr(driver.datasets, "provider_session", held_session)
    real_capture = selected.verification_context.capture
    def capture(tasks, *args, **kwargs):
        assert events == ["enter"]
        assert [task["task_id"] for task in tasks] == ["t01"]
        events.append("capture")
        return real_capture(tasks, *args, **kwargs)
    monkeypatch.setattr(selected.verification_context, "capture", capture)
    class ClaimReached(Exception):
        pass
    def claim(args, run_id, fingerprint):
        assert events == ["enter", "capture"]
        assert args.verification_context["sha256"] and args.scoring_identity is None
        events.append("claim")
        raise ClaimReached
    monkeypatch.setattr(driver.run_namespace, "claim", claim)
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ClaimReached):
        driver.main()
    assert events == ["enter", "capture", "claim", "exit"]


def test_driver_cancellation_during_selected_admission_closes_session_without_claim(driver_setup, monkeypatch, tmp_path):
    import sys
    driver, argv = driver_setup
    events = []
    real_session = driver.datasets.provider_session
    @contextmanager
    def held_session():
        events.append("enter")
        with real_session():
            try:
                yield
            finally:
                events.append("exit")
    monkeypatch.setattr(driver.datasets, "provider_session", held_session)
    def cancel(*args, **kwargs):
        events.append("cancel")
        raise KeyboardInterrupt("authored selected admission cancellation")
    monkeypatch.setattr(selected.readiness, "selected_model_checks", cancel)
    monkeypatch.setattr(driver.run_namespace, "claim", lambda *a: pytest.fail("claimed before admission"))
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(KeyboardInterrupt, match="authored selected"):
        driver.main()
    assert events == ["enter", "cancel", "exit"]
    assert not (tmp_path / "runs").exists() and not (tmp_path / "work").exists()


def test_prepare_only_bypasses_plan_and_refused_admission_does_not_consume_reservation(driver_setup, monkeypatch, tmp_path):
    import sys
    driver, argv = driver_setup
    def refuse(*args, **kwargs):
        raise selected.SelectedRunRefused("authored selected admission refusal")
    monkeypatch.setattr(driver, "build_selected_run_plan", refuse)
    monkeypatch.setattr(sys, "argv", [*argv, "--prepare-only"])
    assert driver.main() == 0
    run = tmp_path / "runs/d1-control"
    before = {path.name: path.read_bytes() for path in run.iterdir()}
    assert "prepared-reservation.json" in before
    assert json.loads(before["run_meta.json"])["launched"] is False
    monkeypatch.setattr(sys, "argv", argv)
    assert driver.main() == 2
    assert {path.name: path.read_bytes() for path in run.iterdir()} == before
    assert not (tmp_path / "work/d1-control").exists()


@pytest.mark.parametrize("stage", ["manifest", "_check_env_kinds", "_resolve_envs", "context"])
@pytest.mark.parametrize("error_type", [OSError, TypeError, ValueError])
def test_environment_context_exception_boundary_includes_capability_and_resolver(setup, monkeypatch, stage, error_type):
    error = error_type("authored selected environment boundary")
    def fail(*args, **kwargs):
        raise error
    if stage == "context":
        monkeypatch.setattr(selected.verification_context, "capture", fail)
    else:
        monkeypatch.setattr(selected, stage, fail)
    with pytest.raises(selected.SelectedRunRefused) as caught:
        build(setup)
    assert caught.value.__cause__ is error
    assert str(caught.value) == "invalid verification context: authored selected environment boundary"
    assert caught.value.channel == "console"


@pytest.mark.parametrize("error_type", [OSError, TypeError])
def test_scenario_manifest_exception_keeps_its_narrower_boundary(setup, monkeypatch, error_type):
    options, source, harness, reporter = setup
    error = error_type("authored scenario manifest boundary")
    def fail(*args):
        raise error
    monkeypatch.setattr(selected, "manifest", fail)
    with source.session(), pytest.raises(error_type) as caught:
        selected.build_selected_run_plan(options, source, runs_root=harness.parent,
            scenario={"model": {"backend": "mock"}, "env_kinds": ["files"]},
            model={"backend": "mock"}, harness_source=harness, policy_evidence=None, reporter=reporter)
    assert caught.value is error
