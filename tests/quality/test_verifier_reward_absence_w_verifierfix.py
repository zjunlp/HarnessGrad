"""Snapshot reward transport controls; no Docker, model or dataset execution."""
from contextlib import nullcontext
import json
import subprocess

import pytest

from eval.run import runner
from eval.verification import verifier


@pytest.fixture
def snapshot_check(tmp_path, monkeypatch):
    """Follow test_verifier.py's mocked transport idiom at the snapshot seam."""
    removed = []

    def denied(*args, **kwargs):
        pytest.fail("unexpected Docker transport")

    monkeypatch.setattr(verifier.container_mod, "docker", denied)
    monkeypatch.setattr(verifier.container_mod, "require_snapshot_identity", lambda *a: None)
    monkeypatch.setattr(verifier.container_mod, "require_snapshot_container", lambda *a: None)
    monkeypatch.setattr(verifier.container_mod, "snapshot_startup",
                        lambda: nullcontext(("synthetic-idle", ())))
    monkeypatch.setattr(verifier.container_mod, "create", lambda *a, **k: None)
    monkeypatch.setattr(verifier.container_mod, "start", lambda *a: None)
    monkeypatch.setattr(verifier.container_mod, "remove_container", removed.append)

    def check(raw, exit_code=3, require_exit_zero=False):
        def cp_out(name, source, destination):
            assert not (require_exit_zero and exit_code), 'failed scorer reward must not be collected'
            assert source == "/verifier/reward.txt"
            if raw is None:
                return False
            destination.write_text(raw)
            return True

        monkeypatch.setattr(verifier.container_mod, "cp_out", cp_out)
        monkeypatch.setattr(verifier.container_mod, "exec_in", lambda *a, **k:
                            subprocess.CompletedProcess(
                                [], exit_code, "check starting\n", "check dependency unavailable\n"))
        result = verifier._verify_container_state(
            "synthetic-snapshot", {"kind": "command", "argv": ["synthetic-check"],
                                   "reward_file": "/verifier/reward.txt",
                                   "result_contract": {"version": 1, "score_range": [0, 1],
                                                       "require_exit_zero": require_exit_zero}},
            expected_snapshot="synthetic-snapshot", task_mount="/app", staging=tmp_path,
            timeout_s=5, run_id="synthetic-verifierfix", task_id="synthetic-task")
        assert len(removed) == 1
        return result

    return check


def test_snapshot_strict_completion_rejects_existing_reward(snapshot_check):
    result = snapshot_check('1', exit_code=78, require_exit_zero=True)
    assert result['score'] is None and result['outcome'] == 'unmeasured'
    assert result['exit_code'] == 78


@pytest.mark.parametrize("exit_code", [0, 3])
def test_missing_snapshot_reward_is_invalid(snapshot_check, tmp_path, monkeypatch, exit_code):
    verdict = snapshot_check(None, exit_code)
    monkeypatch.setattr(runner, "run_one", lambda *a, **k: {
        "verdict": verdict, "trace": "synthetic trace\n", "exit_code": 0, "stderr": ""})
    cache = {}
    result = runner._evaluate(
        tmp_path, [{"task_id": "synthetic-task"}], {}, cache=cache,
        sandbox=False, trials=1, jobs=1, progress_dir=tmp_path)
    print(json.dumps({"exit_code": exit_code, "verdict": verdict,
                      "per_task": result["per_task"], "invalid": result["invalid"]}, sort_keys=True))
    assert verdict.get("unmeasured"), "a missing reward is unmeasured, never a zero"
    assert verdict["passed"] is False and "score" not in verdict
    assert "wrote no /verifier/reward.txt" in verdict["unmeasured"]
    assert f"exit {exit_code}" in verdict["detail"]
    assert "stdout check starting" in verdict["detail"]
    assert "stderr check dependency unavailable" in verdict["detail"]
    assert result["invalid"] == {
        "synthetic-task": {"stage": "check", "detail": verdict["unmeasured"]}}
    assert result["per_task"] == {} and cache == {}
    record = json.loads((tmp_path / "progress/round-0/synthetic-task.json").read_text())
    assert record["invalid"] == result["invalid"]["synthetic-task"]
    assert "stdout check starting" in record["invalid"]["detail"]
    assert "stderr check dependency unavailable" in record["invalid"]["detail"]
    assert "score" not in record


@pytest.mark.parametrize("raw, score", [("0\n", 0.0), ("0.5\n", 0.5), ("1\n", 1.0)])
def test_present_snapshot_reward_keeps_numeric_score(snapshot_check, raw, score):
    verdict = snapshot_check(raw)
    assert verdict["score"] == score and verdict["passed"] is bool(score)
    assert "unmeasured" not in verdict
    assert f"reward {score:g} from /verifier/reward.txt (exit 3)" in verdict["detail"]
    assert "stdout check starting" in verdict["detail"]
    assert "stderr check dependency unavailable" in verdict["detail"]


def test_present_malformed_snapshot_reward_keeps_existing_failure(snapshot_check):
    verdict = snapshot_check("not-a-number\n")
    assert verdict["passed"] is False
    assert "score" not in verdict and "unmeasured" not in verdict
    assert verdict["detail"] == "/verifier/reward.txt held 'not-a-number', which is not a number"
