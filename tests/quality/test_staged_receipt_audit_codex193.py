"""Receipt correctness controls using authored data and stubbed execution only."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest
import jsonschema

from eval.policy import diagnostic_policy as policy
from eval.run import runner
from lifecycle_fixtures_codex177 import completed_lifecycle, summaries

ROOT = Path(__file__).resolve().parents[2]
SELECTED = ["synthetic-task-193"]
SCHEMA = json.loads((ROOT / "eval/diagnostic_receipt.schema.json").read_text())


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("receipt audit attempted external execution")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(runner.container_mod, "docker", refuse)


@pytest.fixture
def context():
    return dict(dataset="demo", side="train",
                metric=dict(name="synthetic metric", scorer_sha256="b" * 64),
                hashes={name: "c" * 64 for name in policy.HASH_NAMES},
                endpoint=policy.ENDPOINTS[0],
                limits=dict(resource=dict(bytes=1024), budget=dict(calls=1)),
                gates=dict(status="passed", receipt_sha256="a" * 64, detail="Synthetic gates"),
                comparability_notes=["Authored synthetic evidence; no production authorization."])


def pending_receipt(context):
    unknown = dict(status="unknown", receipt_sha256=None, detail="No backend observations")
    return policy.build_receipt(run_id="synthetic-run-193", stage="diagnostic",
                                eligible=SELECTED, selected=SELECTED, context=context,
                                outcomes=[], cleanup=unknown, snapshot=unknown)


@pytest.mark.parametrize("field", ["gates", "cleanup", "snapshot"])
def test_schema_and_runtime_require_hash_for_passed_summary(context, field):
    receipt = pending_receipt(context)
    summary = receipt["context"][field] if field == "gates" else receipt[field]
    summary.update(status="passed", receipt_sha256=None)
    with pytest.raises(ValueError, match="passed requires a receipt hash"):
        policy.validate_receipt(receipt)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(receipt, SCHEMA)


@pytest.mark.parametrize("exit_code", [-1, 1, 137, None, True])
@pytest.mark.parametrize("score", [0.0, 1.0])
def test_staged_adapter_checks_explicit_execution_output(exit_code, score):
    result = dict(trials=1, per_task={SELECTED[0]: score}, harness_failed={},
                  harness_output={SELECTED[0]: dict(exit_code=exit_code, stderr="Synthetic exit")})
    outcome = policy.evaluation_outcomes(result, SELECTED)[0]
    assert outcome["status"] == ("invalid" if type(exit_code) is int else "unmeasured")
    assert outcome["score"] is None and outcome["reason"]


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_completed_zero_exit_retains_metric_score(score):
    result = dict(trials=1, per_task={SELECTED[0]: score},
                  harness_output={SELECTED[0]: dict(exit_code=0, stderr="")})
    assert policy.evaluation_outcomes(result, SELECTED) == [dict(
        task_id=SELECTED[0], trial=1, status="measured", score=score, reason=None)]


@pytest.mark.parametrize("timed_out", [False, True])
def test_actual_run_one_shape_survives_evaluation_and_staged_adapter(
        monkeypatch, tmp_path, timed_out):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps(dict(name="synthetic", entrypoint="unused.py")))
    trace = json.dumps(dict(usage=dict(input_tokens=1, output_tokens=1, calls=1))) + "\n"

    def stub_launch(*args, **kwargs):
        # Authored outputs stand in for a process; no entrypoint is ever executed.
        workdir = Path(kwargs["task_dir"])
        (workdir / "trace.jsonl").write_text(trace)
        (workdir / "answer.txt").write_text("Synthetic answer")
        if timed_out:
            raise subprocess.TimeoutExpired("synthetic-launch", 1)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(runner.sandbox_mod, "run", stub_launch)
    monkeypatch.setattr(runner, "harness_env", lambda: {})
    monkeypatch.setattr(runner.harness_runtime, "lookup", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_verify", lambda *a, **k: dict(passed=False, score=0.0))
    monkeypatch.setattr(runner, "snapshot", lambda *a: {})
    progress_dir = tmp_path / "run"
    result = runner.evaluate(repo, [dict(task_id=SELECTED[0])], {}, trials=1, jobs=1,
                             progress_dir=progress_dir)
    assert result["harness_failed"] == {}  # Usage survives the timeout; this flag is insufficient.
    assert result["harness_output"][SELECTED[0]]["exit_code"] == (-1 if timed_out else 0)
    outcome = policy.evaluation_outcomes(result, SELECTED)[0]
    assert outcome["status"] == ("invalid" if timed_out else "measured")
    assert outcome["score"] == (None if timed_out else 0.0)
    # Progress still reports the general runner's provisional measurement. It
    # carries no lifecycle gate and must not be consumed as a staged publication.
    progress = json.loads((progress_dir / f"progress/round-0/{SELECTED[0]}.json").read_text())
    assert progress["score"] == 0.0 and progress["complete"] is True
    assert "lifecycle" not in progress


@pytest.fixture
def stubbed_driver(monkeypatch, tmp_path, context):
    import driver
    evidence = tmp_path / "policy.json"
    evidence.write_text(json.dumps(dict(context=context)))
    run_id = "synthetic-driver-193"
    runs = tmp_path / "runs"
    work_root = tmp_path / "work"
    argv = ["driver.py", "--dataset", "demo", "--policy-evidence", str(evidence),
            "--run-id", run_id, "--runs-root", str(runs), "--work-root", str(work_root)]
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(driver, "load_env", lambda *a: {})
    monkeypatch.setattr(driver, "describe_model", lambda: dict(model="synthetic", backend="openai", live=True))
    monkeypatch.setattr(driver.platform_config, "current", lambda: SimpleNamespace(
        agent=SimpleNamespace(base_url=context["endpoint"])))
    monkeypatch.setattr(driver, "load_improvers", lambda: {})
    monkeypatch.setattr(driver, "forbidden_reason", lambda *a: None)
    monkeypatch.setattr(driver.datasets, "load_split", lambda *a: (
        [dict(task_id=SELECTED[0])], {}, None))
    monkeypatch.setattr(driver.datasets, "oracle_source", lambda *a: ("synthetic", 0))
    monkeypatch.setattr(driver.datasets, "load_tasks", lambda *a, **kwargs: ([], {}, {}))
    monkeypatch.setattr(driver.datasets, "load_envs", lambda *a, **kwargs: {})
    monkeypatch.setattr(driver, "_warn_missing_overlay", lambda *a: None)
    monkeypatch.setattr(driver, "_check_env_kinds", lambda *a: None)
    monkeypatch.setattr(driver, "_resolve_envs", lambda envs: (envs, None))
    # The workspace boundary is stubbed so all scratch writes stay in this worktree.
    monkeypatch.setattr(driver, "workspace_is_outside_platform", lambda *a: True)
    monkeypatch.setattr(driver.sandbox, "self_check", lambda *a: dict(ok=True, detail="Synthetic sandbox"))
    monkeypatch.setattr(driver.console, "attach", lambda *a: None)
    monkeypatch.setattr(driver.console, "note", lambda *a, **k: None)
    monkeypatch.setattr(driver.console, "header", lambda *a, **k: None)
    monkeypatch.setattr(driver, "_write_run_meta", lambda *a, **k: None)
    monkeypatch.setattr(driver, "stage_workspace", lambda *a: None)
    monkeypatch.setattr(driver, "manifest", lambda *a: dict(name="synthetic", version="synthetic"))
    monkeypatch.setattr(driver, "commit_state", lambda *a: "synthetic-commit")
    monkeypatch.setattr(driver, "_probe_tool_calls", lambda *a: pytest.fail("Model calls forbidden"))
    monkeypatch.setattr(driver, "evaluate", lambda *a, **k: pytest.fail("Unstubbed H0 forbidden"))
    for key in ("HARNESSGRAD_WORK_ROOT", "HARNESSGRAD_SEED", "HG_IMPROVER_NAME"):
        monkeypatch.delenv(key, raising=False)
    return driver, runs / run_id, work_root / run_id / "workspace"


@pytest.mark.parametrize("workspace_exists", [False, True])
def test_existing_staged_workspace_is_preserved(monkeypatch, stubbed_driver, workspace_exists):
    driver, run_dir, workspace = stubbed_driver
    evidence_root = workspace if workspace_exists else workspace.parent
    evidence_root.mkdir(parents=True)
    saved = evidence_root / "old-evidence.txt"
    saved.write_text("Synthetic incomplete workspace")
    monkeypatch.setattr(driver.services_mod, "reap", lambda *a: pytest.fail("Must refuse before reaping"))
    assert driver.main() == 2
    assert saved.read_text() == "Synthetic incomplete workspace"
    plan = json.loads((run_dir / "staged_plan.json").read_text())
    policy.validate_receipt(plan)
    assert plan["score"] is None and plan["coverage"]["unmeasured_trials"] == 1


@pytest.mark.parametrize("result_kind", ["score", "invalid", "exception"])
def test_driver_preserves_pending_plan_and_refuses_publication(
        monkeypatch, stubbed_driver, result_kind):
    driver, run_dir, workspace = stubbed_driver
    monkeypatch.setattr(driver.services_mod, "reap", lambda *a: pytest.fail("Fresh staged ID must not reap"))
    result = dict(trials=1, per_task={SELECTED[0]: 0.0}, invalid={}, tampered={},
                  harness_failed={}, harness_output={SELECTED[0]: dict(exit_code=0, stderr="")})
    if result_kind == "invalid":
        result["invalid"][SELECTED[0]] = dict(stage="check", detail="Synthetic scorer failure")

    def observe(*args, **kwargs):
        plan = json.loads((run_dir / "staged_plan.json").read_text())
        policy.validate_receipt(plan)
        assert plan["score"] is None and plan["coverage"]["unmeasured_trials"] == 1
        if result_kind == "exception":
            raise RuntimeError("Synthetic interruption")
        return result

    monkeypatch.setattr(driver, "evaluate", observe)
    monkeypatch.setattr(driver, "_report_invalid", lambda *a: None)
    monkeypatch.setattr(driver, "_report_harness_failed", lambda *a: None)
    if result_kind == "exception":
        with pytest.raises(RuntimeError, match="Synthetic interruption"):
            driver.main()
        assert not (run_dir / "diagnostic_receipt.json").exists()
    else:
        assert driver.main() == 2
        final = json.loads((run_dir / "diagnostic_receipt.json").read_text())
        policy.validate_receipt(final)
        jsonschema.validate(final, SCHEMA)
        assert final["score"] is None and final["lifecycle"] is None
        assert final["coverage"]["invalid_trials" if result_kind == "invalid" else "unmeasured_trials"] == 1
    original = (run_dir / "staged_plan.json").read_bytes()
    assert driver.main() == 2
    assert (run_dir / "staged_plan.json").read_bytes() == original
    assert not (run_dir / "curve.jsonl").exists()


def test_schema_is_shape_only_and_replay_remains_required(context):
    outcomes = [dict(task_id=SELECTED[0], trial=1, status="measured", score=0.0, reason=None)]
    lifecycle = completed_lifecycle(context, SELECTED, SELECTED, outcomes, run_id="synthetic-run-193")
    cleanup, snapshot = summaries(lifecycle)
    receipt = policy.build_receipt(run_id="synthetic-run-193", stage="diagnostic",
                                   eligible=SELECTED, selected=SELECTED, context=context,
                                   outcomes=outcomes, lifecycle=lifecycle, cleanup=cleanup, snapshot=snapshot)
    policy.validate_receipt(receipt)
    jsonschema.validate(receipt, SCHEMA)
    incomplete = deepcopy(receipt)
    incomplete["lifecycle"]["events"] = []
    jsonschema.validate(incomplete, SCHEMA)
    with pytest.raises(ValueError, match="inconsistent lifecycle"):
        policy.validate_receipt(incomplete)
