"""Synthetic execution at the runtime boundary; no dataset, model or Docker calls."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from eval.policy import diagnostic_policy as policy
from eval.policy.unobserved_diagnostic import Authorization, REASON
from eval.run import runner
from test_staged_receipt_audit_codex193 import context, stubbed_driver, SELECTED

TASK = {"task_id": SELECTED[0], "goal": "synthetic diagnostic"}
EXEC = {"kind": "exec", "state": "container", "network": "none", "image": "synthetic:no-execution"}
AUTH = Authorization("synthetic-driver-193", TASK["task_id"], "Operator authorizes synthetic diagnostic")
SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "eval/diagnostic_receipt.schema.json").read_text())


@pytest.fixture
def synthetic_runtime(monkeypatch):
    calls = []
    def execute(*args, **kwargs):
        calls.append((args, kwargs))
        return dict(task_id=TASK["task_id"], trace='{"synthetic":true}\n',
                    exit_code=0, stderr="", verdict=dict(passed=True, score=0.25))
    monkeypatch.setattr(runner, "_run_one", execute)
    monkeypatch.setattr(runner, "snapshot", lambda *a: {})
    return calls


def test_unauthorized_exec_refuses_before_launch_and_cache(tmp_path, synthetic_runtime):
    result = runner.run_one(tmp_path, TASK, env=EXEC)
    assert result["invalid"]["receipt"]["reason"] == "backend_observation_unavailable"
    cache = {f"sha:{TASK['task_id']}:1": {"score": 1}}
    result = runner.evaluate(tmp_path, [TASK], {}, cache, harness_sha="sha",
                             envs={TASK["task_id"]: EXEC})
    assert result["invalid"][TASK["task_id"]]["receipt"]["measured"] is False
    assert not synthetic_runtime


def test_authorized_exec_runs_and_tags_every_result_without_cache_publication(tmp_path, synthetic_runtime):
    cache = {}
    direct = runner.run_one(tmp_path, TASK, env=EXEC, run_id=AUTH.run_id,
                            diagnostic_authorization=AUTH)
    assert direct["enforcement"] == AUTH.record()
    result = runner.evaluate(tmp_path, [TASK], {}, cache, envs={TASK["task_id"]: EXEC},
                             run_id=AUTH.run_id, diagnostic_authorization=AUTH,
                             progress_dir=tmp_path / "records")
    assert len(synthetic_runtime) == 2
    assert result["enforcement"] == AUTH.record()
    assert result["per_task"] == {TASK["task_id"]: 0.25}  # raw scorer fact only
    assert result["scorer_isolation_class"] == "unknown"
    assert result["scorer_isolation_comparable"] is False
    assert cache == {}
    progress = json.loads((tmp_path / "records/progress/round-0" / f"{TASK['task_id']}.json").read_text())
    assert progress["enforcement"] == AUTH.record()
    assert progress["scorer_isolation_class"] == "unknown"
    assert progress["scorer_isolation_comparable"] is False
    assert progress["score"] == 0.25 and progress["complete"] is True


@pytest.mark.parametrize("change", [dict(run_id="other"), dict(trials=2), dict(jobs=2),
                                    dict(round_no=1), dict(tasks=[TASK, TASK]),
                                    dict(cache={"old": {"score": 1}})])
def test_authorization_cannot_expand_scope(tmp_path, synthetic_runtime, change):
    args = dict(repo=tmp_path, tasks=[TASK], scorable={}, envs={TASK["task_id"]: EXEC},
                run_id=AUTH.run_id, diagnostic_authorization=AUTH)
    args.update(change)
    with pytest.raises(ValueError):
        runner.evaluate(**args)
    assert not synthetic_runtime


@pytest.fixture
def diagnostic_driver(monkeypatch, stubbed_driver):
    driver, run_dir, workspace = stubbed_driver
    import sys
    evidence = Path(sys.argv[sys.argv.index("--policy-evidence") + 1])
    content = json.loads(evidence.read_text())
    content["context"]["gates"] = dict(status="unknown", receipt_sha256=None,
                                      detail="Policy enforcement not observed")
    evidence.write_text(json.dumps(content))
    monkeypatch.setattr(driver.datasets, "load_envs", lambda *a, **kwargs: {TASK["task_id"]: EXEC})
    monkeypatch.setattr(driver.container, "available", lambda: "synthetic")
    monkeypatch.setattr(driver.container, "self_check", lambda *a: dict(ok=True, detail="synthetic"))
    monkeypatch.setattr(driver.platform_config, "current", lambda: SimpleNamespace(
        agent=SimpleNamespace(base_url=policy.ENDPOINTS[0], backend="openai")))
    monkeypatch.setattr(driver.platform_config, "dump", lambda **k: {})
    monkeypatch.setattr(driver, "evaluate", runner.evaluate)
    from harnessgrad.records import _write_run_meta
    monkeypatch.setattr(driver, "_write_run_meta", _write_run_meta)
    monkeypatch.setattr(driver, "_write_curve", lambda *a: pytest.fail("No diagnostic curve allowed"))
    return driver, run_dir


def test_unknown_gates_without_operator_permission_still_refuse(diagnostic_driver, synthetic_runtime):
    driver, run_dir = diagnostic_driver
    assert driver.main() == 2
    assert not synthetic_runtime and not run_dir.exists()


def test_operator_authorized_driver_runs_once_but_cannot_publish_or_expand(
        monkeypatch, diagnostic_driver, synthetic_runtime):
    import sys
    driver, run_dir = diagnostic_driver
    monkeypatch.setattr(sys, "argv", sys.argv + ["--authorize-unobserved-diagnostic", AUTH.operator_authorization])
    assert driver.main() == 2  # execution may finish, comparable publication must refuse
    assert len(synthetic_runtime) == 1
    progress = json.loads((run_dir / "progress/round-0" / f"{TASK['task_id']}.json").read_text())
    assert progress["enforcement"] == AUTH.record() and progress["complete"] is True
    assert progress["scorer_isolation_class"] == "unknown"
    assert progress["scorer_isolation_comparable"] is False
    receipt = json.loads((run_dir / "diagnostic_receipt.json").read_text())
    policy.validate_receipt(receipt)
    jsonschema.validate(receipt, SCHEMA)
    assert receipt["score"] is None and receipt["coverage"]["measured_trials"] == 0
    assert receipt["coverage"]["unmeasured_trials"] == 1
    assert receipt["outcomes"][0]["reason"] == REASON
    for name in ("staged_plan.json", "diagnostic_receipt.json", "run_meta.json", "diagnostic_execution.json"):
        assert json.loads((run_dir / name).read_text())["enforcement"] == AUTH.record()
    assert not (run_dir / "curve.jsonl").exists()
    with pytest.raises(ValueError, match="cannot authorize expansion"):
        policy.require_review(dict(receipt=receipt), context=receipt["context"], eligible=SELECTED)
    forged = copy.deepcopy(receipt)
    forged["enforcement"]["measured"] = True
    with pytest.raises(ValueError):
        policy.validate_receipt(forged)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(forged, SCHEMA)
    before = (run_dir / "diagnostic_receipt.json").read_bytes()
    assert driver.main() == 2
    assert len(synthetic_runtime) == 1 and (run_dir / "diagnostic_receipt.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["preflight", "exception"])
def test_incomplete_authorized_attempt_has_final_nonobservation_receipt(
        monkeypatch, diagnostic_driver, synthetic_runtime, failure):
    import sys
    driver, run_dir = diagnostic_driver
    monkeypatch.setattr(sys, "argv", sys.argv + ["--authorize-unobserved-diagnostic", AUTH.operator_authorization])
    if failure == "preflight":
        monkeypatch.setattr(driver, "_resolve_envs", lambda envs: (envs, "missing synthetic image"))
        assert driver.main() == 2
        assert not synthetic_runtime
    else:
        def fail(*a, **k):
            raise FileNotFoundError("missing synthetic runtime file")
        monkeypatch.setattr(runner, "_run_one", fail)
        with pytest.raises(FileNotFoundError, match="missing synthetic runtime"):
            driver.main()
    receipt = json.loads((run_dir / "diagnostic_receipt.json").read_text())
    policy.validate_receipt(receipt)
    assert receipt["enforcement"] == AUTH.record()
    assert receipt["coverage"]["invalid_trials"] == 1 and receipt["score"] is None
    assert not (run_dir / "curve.jsonl").exists()


@pytest.mark.parametrize("extra", [["--sampling", "all"], ["--sampling", "pilot_10pct"],
                                   ["--rounds", "1"], ["--trials", "2"], ["--jobs", "2"],
                                   ["--side", "eval"]])
def test_operator_flag_never_authorizes_other_driver_paths(monkeypatch, diagnostic_driver, synthetic_runtime, extra):
    import sys
    driver, run_dir = diagnostic_driver
    monkeypatch.setattr(sys, "argv", sys.argv + ["--authorize-unobserved-diagnostic", AUTH.operator_authorization] + extra)
    assert driver.main() == 2
    assert not synthetic_runtime and not run_dir.exists()
