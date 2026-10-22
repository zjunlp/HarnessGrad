"""Native pristine task/run/trial binding, including hostile producer results."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from eval.admission import artifact_admission as artifacts
from eval.admission.pilot_admission import AdmissionError
from eval.run import runner
from eval.verification import verifier
from test_scorer_isolation_option_b import actor, docker_double, scorer


IDENTITY = dict(task_id="t", outer_run_id="binding-control", trial_id="round-0.harness-.trial-1")


def invoke(source, scorer, **changes):
    identity = {**IDENTITY, **changes}
    return verifier._verify(source, scorer, "", sandbox=False, timeout_s=5,
                            task_id=identity["task_id"], run_id=identity["outer_run_id"],
                            trial_id=identity["trial_id"])


@pytest.mark.parametrize("omitted", ["task_id", "run_id", "trial_id"])
def test_missing_caller_identity_refuses_before_export(tmp_path, scorer, docker_double, omitted):
    inputs = dict(task_id="t", run_id="binding-control", trial_id="trial-1")
    inputs.pop(omitted)
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, **inputs)
    assert verdict["score"] is None and verdict["outcome"] == "unmeasured"
    assert "identity" in verdict["unmeasured"] and not docker_double.calls


def test_bound_identity_survives_native_verdict(tmp_path, scorer, docker_double):
    verdict = invoke(actor(tmp_path), scorer)
    assert verdict["score"] == 1 and verdict["outcome"] == "measured"
    evidence = json.loads(json.dumps(verdict))["scorer_isolation_evidence"]
    binding = evidence["execution_binding"]
    assert {k: binding[k] for k in IDENTITY} == IDENTITY
    assert evidence["export"]["schema"] == "harnessgrad.state-export-receipt.v2"
    assert evidence["export"]["execution_binding"] == IDENTITY
    assert evidence["runtime"]["execution_binding"] == IDENTITY
    assert binding["runtime_export_run_id"] == evidence["export"]["run_id"] == evidence["cleanup"]["container"]


@pytest.mark.parametrize("dimension", list(IDENTITY))
def test_foreign_export_never_starts(tmp_path, scorer, docker_double, monkeypatch, dimension):
    """Real bounded export under a different caller identity; runtime UUID matches."""
    original = artifacts.export_state

    def foreign(*args, **kwargs):
        kwargs["execution_binding"] = {**kwargs["execution_binding"], dimension: "different"}
        if dimension == "task_id":
            # A different task needs its own genuine sealed contract too.
            args = list(args)
            contract = json.loads(args[2])
            contract["task_id"] = "different"
            args[2] = json.dumps(contract).encode()
            args[3] = hashlib.sha256(args[2]).hexdigest()
        return original(*args, **kwargs)

    monkeypatch.setattr(artifacts, "export_state", foreign)
    verdict = invoke(actor(tmp_path), scorer)
    assert verdict["score"] is None and verdict["outcome"] == "unmeasured"
    assert "identity mismatch" in verdict["unmeasured"]
    assert not docker_double.started
    assert verdict["scorer_isolation_evidence"]["cleanup"]["removed"]


def test_v1_cannot_downgrade_bound_export(tmp_path, scorer, docker_double, monkeypatch):
    original = artifacts.export_state

    def unbound(*args, **kwargs):
        kwargs.pop("execution_binding")
        return original(*args, **kwargs)

    monkeypatch.setattr(artifacts, "export_state", unbound)
    verdict = invoke(actor(tmp_path), scorer)
    assert verdict["score"] is None and not docker_double.started
    assert "identity mismatch" in verdict["unmeasured"]


@pytest.mark.parametrize("dimension", list(IDENTITY))
def test_foreign_verdict_is_refused_by_aggregation(tmp_path, scorer, docker_double, monkeypatch, dimension):
    verdict = invoke(actor(tmp_path), scorer)
    assert verdict["score"] == 1
    # Present an actual verdict to a consumer requesting a different identity.
    expected = {**IDENTITY, dimension: "different"}
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_kw:
                        dict(verdict=verdict, trace="", exit_code=0, stderr=""))
    measured = runner._evaluate(tmp_path, [dict(task_id=expected["task_id"])], {},
        run_id=expected["outer_run_id"], round_no=1 if dimension == "trial_id" else 0,
        verifiers={expected["task_id"]: scorer}, sandbox=False)
    assert measured["per_task"] == {}
    assert "binding" in json.dumps(measured["invalid"])


@pytest.mark.parametrize("foreign_index", [0, 1, 2])
def test_foreign_trial_verdict_stays_unmeasured_when_declared_trials_continue(
        tmp_path, scorer, docker_double, monkeypatch, foreign_index):
    work = actor(tmp_path)
    calls = []

    def run_one(_repo, _task, **kwargs):
        trial_id = "foreign-trial" if len(calls) == foreign_index else kwargs["trial_id"]
        calls.append(kwargs["run_seed"])
        verdict = invoke(work, scorer, trial_id=trial_id)
        assert verdict["score"] == 1
        return dict(verdict=verdict, trace="", exit_code=0, stderr="")

    monkeypatch.setattr(runner, "run_one", run_one)
    protocol = dict(version=1, metric="pass^k", trials=3, ks=[1, 2, 3], seeds=[10, 20, 30])
    result = runner._evaluate(tmp_path, [{"task_id": "t"}], {}, trials=3,
        run_id=IDENTITY["outer_run_id"], sandbox=False,
        verifiers={"t": {**scorer, "trial_protocol": protocol}}, progress_dir=tmp_path / "records")
    assert calls == ["10", "20", "30"]
    report = result["repeated_trials"]
    assert report["missing"] == [] and len(report["invalid"]) == 1
    assert not report["complete"] and report["metrics"] is None
    for index, row in enumerate(report["results"]):
        if index == foreign_index:
            assert row["status"] == "unmeasured" and row["score"] is None
            assert "binding refused" in row["reason"]
        else:
            assert row["status"] == "measured" and row["score"] == 1


def test_verifier_dispatch_refuses_foreign_returned_verdict(tmp_path, scorer, docker_double, monkeypatch):
    from eval.verification import pristine
    verdict = invoke(actor(tmp_path), scorer)
    monkeypatch.setattr(pristine, "verify", lambda *_a, **_kw: verdict)
    refused = invoke(tmp_path / "unused", scorer, trial_id="different")
    assert refused["score"] is None and "binding" in refused["unmeasured"]


def test_outer_runner_threads_distinct_trials_and_refuses_stale_cache(tmp_path, scorer, docker_double):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps(dict(name="fixture", version="1", entrypoint="agent.py")))
    (repo / "agent.py").write_text(
        "import argparse,pathlib\n"
        "p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir')\n"
        "a=p.parse_args();pathlib.Path(a.workdir,'answer.txt').write_text('correct')\n")
    cache = {}
    first = runner._evaluate(repo, [dict(task_id="t")], {}, run_id="first-run", trials=2,
        round_no=2, harness_sha="revision-a", verifiers={"t": scorer}, cache=cache, sandbox=False)
    assert first["per_task"] == {"t": 1.0}
    ids = [v["scorer_isolation_evidence"]["execution_binding"] for v in first["trial_verdicts"]["t"]]
    assert [b["trial_id"] for b in ids] == ["round-2.harness-revision-a.trial-1", "round-2.harness-revision-a.trial-2"]
    assert all(b["outer_run_id"] == "first-run" for b in ids)
    second = runner._evaluate(repo, [dict(task_id="t")], {}, run_id="second-run", trials=2,
        round_no=2, harness_sha="revision-a", verifiers={"t": scorer}, cache=copy.deepcopy(cache), sandbox=False)
    assert second["per_task"] == {} and "binding" in json.dumps(second["invalid"])


@pytest.mark.parametrize("value", [None, "", "   ", 1, True, "bad\nidentity"])
def test_invalid_trial_labels_fail_closed(tmp_path, scorer, docker_double, value):
    verdict = invoke(actor(tmp_path), scorer, trial_id=value)
    assert verdict["score"] is None and not docker_double.calls


def test_bound_low_level_export_requires_independent_expected_identity(tmp_path, scorer):
    raw = Path(scorer["scorer_bundle"]["artifact_contract"]).read_bytes()
    pin = hashlib.sha256(raw).hexdigest()
    root = tmp_path / "export"
    receipt = artifacts.export_state(actor(tmp_path), root, raw, pin,
                                    run_id="local-runtime", execution_binding=IDENTITY)
    with pytest.raises(AdmissionError, match="identity mismatch"):
        artifacts.verify_state_export(root, receipt, raw, pin, run_id="local-runtime")
    artifacts.verify_state_export(root, receipt, raw, pin, run_id="local-runtime", execution_binding=IDENTITY)
