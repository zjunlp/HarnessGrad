"""Authored H0 planning/reuse controls; no benchmark or model calls."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from harnessgrad import config, verification_context
from harnessgrad.orchestration import h0


class Reporter:
    def __init__(self): self.events = []
    def note(self, message, *, level): self.events.append((message, level))


@pytest.fixture
def control(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("HG_AGENT_MODEL", "authored-h0")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "")
    config.reset()
    work = tmp_path / "work"; work.mkdir()
    manifest = {"name": "authored", "version": "1", "entrypoint": "agent.py", "env_kinds": ["files"]}
    (work / "harness.json").write_text(json.dumps(manifest))
    ctx = verification_context.capture([{"task_id": "t1"}], {"t1": "authored"})
    opts = h0.H0Options("demo", "all", "train", 1, 2, 1, False)
    ids = h0.H0Identities(None, None, ctx, None, None)
    attribution = h0.RunAttribution("recipient", "unified", {"name": "current-editor", "kind": "cli"})
    reporter = Reporter()
    yield work, manifest, opts, ids, attribution, reporter
    config.reset()


def donor(tmp_path, control, *, raw=True):
    work, manifest, opts, ids, attribution, reporter = control
    root = tmp_path / "donor"; root.mkdir()
    point = {"run_id": "donor", "round": 0, "per_task": {"t1": .5}, "score": .5,
             "measured_by_platform": True, "verification_context": ids.verification,
             "identity": {"harness_sha": "owned-sha", "agent_model": "authored-h0", "agent_backend": "mock",
                          "jobs": 13, "gpu_budget": 7, "improver": {"name": "donor-editor"}},
             "sampling": {"task_ids": ["t1"]}, "cost": {"harness_tokens": None, "env_generation_tokens": None}}
    (root / "curve.jsonl").write_text(json.dumps(point) + "\n")
    agent = config.current().agent
    (root / "run_meta.json").write_text(json.dumps({"verification_context": ids.verification,
        "config": {"agent": {"model": agent.model, "backend": agent.backend, "base_url": agent.base_url}}}))
    if raw:
        (root / "h0.json").write_text(json.dumps({"h0_sha": "owned-sha", "verification_context": ids.verification,
            "base": {"per_task": {"t1": .5}, "verification_context": ids.verification,
                     "tokens": {"input": None, "output": None}}}))
    traces = root / "round0_traces"; traces.mkdir()
    (traces / "t1.jsonl").write_text('authored trace\n')
    (traces / "t1.trial1.jsonl").write_text('authored trial trace\n')
    return root, point


@pytest.mark.parametrize("raw", [True, False])
def test_raw_and_legacy_donors_reuse_exact_trace_and_measurement_facts(control, tmp_path, raw):
    work, manifest, opts, ids, attribution, reporter = control
    root, point = donor(tmp_path, control, raw=raw)
    loaded = h0.load_h0_donor("donor", tmp_path, "owned-sha")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    h0.admit_h0_donor_records(loaded, work, ids, opts, setups={}, envs={})
    recipient = tmp_path / "recipient"; recipient.mkdir()
    shared = h0.materialize_shared_h0(loaded, recipient, "owned-sha", manifest, attribution, opts, reporter)
    assert shared.base["per_task"] == point["per_task"]
    assert shared.base["traces"] == {"t1": "authored trace\n"}
    assert shared.base["trial_traces"] == {"t1": ["authored trial trace\n"]}
    assert shared.point["cost"] == point["cost"]
    assert shared.point["identity"]["jobs"] == 13 and shared.point["identity"]["gpu_budget"] == 7
    assert shared.point["identity"]["improver"]["name"] == "current-editor"
    assert shared.point["run_id"] == "recipient" and shared.point["shared_from"]["run_id"] == "donor"
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_wrong_payload_sha_refuses_before_curve_is_read(control, tmp_path):
    root, _ = donor(tmp_path, control)
    (root / "curve.jsonl").write_text("not JSON; must not be read")
    with pytest.raises(h0.H0Refused, match="it measured base") as caught:
        h0.load_h0_donor("donor", tmp_path, "different-sha")
    assert caught.value.channel == "stderr"


@pytest.mark.parametrize("filename", ["h0.json", "curve.jsonl"])
def test_decode_errors_are_not_reclassified_as_donor_refusals(control, tmp_path, filename):
    root, _ = donor(tmp_path, control)
    (root / filename).write_text("{authored invalid JSON")
    with pytest.raises(json.JSONDecodeError):
        h0.load_h0_donor("donor", tmp_path, "owned-sha")


def test_missing_exact_context_refuses_without_copying_or_measuring(control, tmp_path):
    work, _, opts, ids, _, _ = control
    root, _ = donor(tmp_path, control)
    path = root / "run_meta.json"; body = json.loads(path.read_text()); body.pop("verification_context")
    path.write_text(json.dumps(body))
    loaded = h0.load_h0_donor("donor", tmp_path, "owned-sha")
    with pytest.raises(h0.H0Refused, match="verification context is missing"):
        h0.admit_h0_donor_records(loaded, work, ids, opts, setups={}, envs={})
    assert not (tmp_path / "recipient").exists()


def test_measurement_port_is_called_exactly_once_with_current_binding_and_cache(control, tmp_path):
    work, _, opts, ids, attribution, _ = control
    cache, calls = {}, []
    expected = {"per_task": {}, "authored_unknown": None}
    def measure(*args, **kwargs):
        calls.append((args, kwargs))
        assert args[3] is cache
        assert kwargs["verification_context"] is ids.verification
        assert kwargs["run_seed"] == "authored-seed"
        return expected
    assert h0.measure_h0(measure, work, [{"task_id": "t1"}], {}, cache, "owned-sha", opts, ids,
        run_dir=tmp_path, attribution=attribution, run_seed="authored-seed", setups={}, verifiers={}, envs={}) is expected
    assert len(calls) == 1


def test_measurement_cancellation_propagates_without_writing_a_payload(control, tmp_path):
    work, _, opts, ids, attribution, _ = control
    error = KeyboardInterrupt("authored H0 cancellation")
    calls = []
    def cancel(*args, **kwargs): calls.append(1); raise error
    with pytest.raises(KeyboardInterrupt) as caught:
        h0.measure_h0(cancel, work, [], {}, {}, "owned-sha", opts, ids, run_dir=tmp_path,
            attribution=attribution, run_seed="authored", setups={}, verifiers={}, envs={})
    assert caught.value is error and calls == [1]
    assert not (tmp_path / "h0.json").exists()


@pytest.mark.parametrize("policy,expected", [("below_0.5", ["a"]), ("above_0.5", ["b"]),
                                            ("diagnostic", ["a", "b"]), ("pilot_10pct", ["a", "b"])])
def test_postmeasurement_selection_keeps_the_original_side(control, policy, expected):
    _, _, opts, _, _, reporter = control
    base = {"per_task": {"a": .25, "b": .75, "outside": 1}}
    selection = h0.select_after_h0(base, [{"task_id": t} for t in ("a", "b")], ["a", "b"], True,
        replace(opts, sampling=policy), reporter)
    assert selection.task_ids == expected and selection.train_ids == expected
    assert selection.sampling["selection_basis_round"] == 0


@pytest.fixture
def driver_control(tmp_path, monkeypatch):
    import driver
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    config.reset()
    def execute(name, *extra):
        import sys
        monkeypatch.setattr(sys, "argv", ["driver.py", "--harness", "loop_plain", "--dataset", "demo",
            "--sampling", "all", "--rounds", "0", "--tasks", "t01", "--improver", "none", "--no-summary",
            "--no-sandbox", "--run-id", name, "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / "work"), *extra])
        return driver.main()
    yield driver, execute
    config.reset()


def test_driver_fresh_h0_calls_once_and_preserves_unknown_cost(driver_control, monkeypatch, tmp_path):
    driver, execute = driver_control
    calls = []
    def measure(repo, tasks, scorable, cache, **kwargs):
        calls.append(kwargs["harness_sha"])
        return {"per_task": {"t01": .5}, "verification_context": kwargs["verification_context"],
                "traces": {}, "trials": 1}
    monkeypatch.setattr(driver, "evaluate", measure)
    assert execute("fresh") == 0
    assert len(calls) == 1
    point = json.loads((tmp_path / "runs/fresh/curve.jsonl").read_text().splitlines()[0])
    assert point["cost"]["harness_tokens"] is None and point["cost"]["env_generation_tokens"] is None


@pytest.mark.parametrize("kind", ["no-score", "cancel"])
def test_driver_h0_failure_never_publishes_a_fabricated_point(driver_control, monkeypatch, tmp_path, kind):
    driver, execute = driver_control
    calls = []
    def measure(*args, **kwargs):
        calls.append(1)
        if kind == "cancel": raise KeyboardInterrupt("authored H0 interruption")
        return {"per_task": {}, "verification_context": kwargs["verification_context"], "traces": {}}
    monkeypatch.setattr(driver, "evaluate", measure)
    if kind == "cancel":
        with pytest.raises(KeyboardInterrupt): execute(kind)
    else:
        assert execute(kind) == 2
    assert calls == [1]
    assert not (tmp_path / "runs" / kind / "curve.jsonl").exists()
    assert (tmp_path / "runs" / kind / "h0.json").exists() is (kind == "no-score")


def test_driver_resume_bypasses_fresh_h0_and_keeps_recorded_ids(driver_control, monkeypatch):
    import sys
    driver, execute = driver_control
    def measure(repo, tasks, scorable, cache, **kwargs):
        return {"per_task": {"t01": .5}, "verification_context": kwargs["verification_context"],
                "traces": {}, "trials": 1}
    monkeypatch.setattr(driver, "evaluate", measure)
    assert execute("continued") == 0
    monkeypatch.setattr(h0, "measure_h0", lambda *a, **k: pytest.fail("resume measured fresh H0"))
    monkeypatch.setattr(h0, "select_after_h0", lambda *a, **k: pytest.fail("resume reselected H0 IDs"))
    monkeypatch.setattr(driver, "select_task_set", lambda *a, **k: pytest.fail("resume reselected saved IDs"))
    class MethodReached(Exception):
        pass
    def method(*args, **kwargs):
        assert args[9] == ["t01"] and kwargs["resumed_from"] == 0
        raise MethodReached
    monkeypatch.setattr(driver, "_run_mode_a", method)
    with pytest.raises(MethodReached):
        execute("continued", "--resume", "--rounds", "1", "--method-entrypoint",
                f"{sys.executable} {Path(driver.__file__).parent / 'methods/noop/run.py'}")
