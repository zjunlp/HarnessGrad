"""Offline worker aggregation contracts with real admission and command verification."""
import json
from pathlib import Path

import pytest

from eval.containers import bun_runtime
from eval.run import cooperative, runner
from test_bun_runtime_contract import fake_runtime, harness


@pytest.fixture
def group(tmp_path, monkeypatch):
    repo = harness(tmp_path)
    fake_runtime(tmp_path, monkeypatch)
    expected = bun_runtime.configure(repo)["identity"]
    workers = [{"id": name, "goal": "offline fixture", "artifacts": ["patch.diff"]}
               for name in ("alice", "bob")]
    check = """import json
from pathlib import Path
record = json.loads(Path('worker_record/workers.json').read_text())
assert set(record['workers']) == {'alice', 'bob'}
Path('verifier/reward.txt').write_text('1')
"""
    verifier = {"kind": "command", "argv": ["/usr/bin/python3", "check.py"],
                "reward_file": "/verifier/reward.txt",
                "inputs": [{"path": "check.py", "content": check}],
                "result_contract": {"version": 1, "score_range": [0, 1]}}
    return repo, expected, {"workers": workers}, verifier


def actor(monkeypatch, *, identity_case="valid", failure=False, bun=True, usage=None):
    """Replace actor execution, retaining run_one receipt promotion and aggregation."""
    def execute(repo, task, *args, runtime_receipt, **kwargs):
        name = task["worker_id"]
        if failure and name == "bob":
            raise RuntimeError("fixture actor failed")
        if bun and not (identity_case == "missing" and name == "bob"):
            receipt = bun_runtime.prepare(repo)
            if identity_case == "all_wrong" or (identity_case == "mismatch" and name == "bob"):
                receipt["identity"] = {**receipt["identity"], "binary_sha256": "wrong"}
            runtime_receipt["bun"] = receipt
        trace = json.dumps({"usage": usage[name]}) if usage is not None else ""
        return {"task_id": task["task_id"], "answer": "", "trace": trace, "exit_code": 0,
                "stderr": "", "verdict": {"kind": "answer", "passed": True},
                "artifacts": {}}
    monkeypatch.setattr(runner, "_run_one", execute)


def run_group(group, tmp_path):
    repo, _, setup, verifier = group
    return runner.run_one(repo, {"task_id": "pair"}, setup=setup, verifier=verifier,
                          run_id="worker-identity", trial_id="1",
                          recordings_root=tmp_path / "archives")


def test_two_native_bun_workers_promote_agreed_identity_and_archive(group, tmp_path, monkeypatch):
    actor(monkeypatch)
    result = run_group(group, tmp_path)
    assert result.get("verdict", {}).get("score") == 1, result.get("verdict")
    assert not result.get("invalid"), result
    expected = group[1]
    assert result.get("harness_runtime", {}).get("bun") == expected
    assert result["runtime_services"]["workers"]["cleanup"]["complete"]
    assert result["runtime_services"]["workers"]["worker_group"] == result["worker_group"]
    archive = Path(result["worker_group"]["archive"])
    archived = json.loads((archive / "workers.json").read_text())
    for name in ("alice", "bob"):
        assert archived["workers"][name]["harness_runtime"]["bun"] == expected


@pytest.mark.parametrize("identity_case", ["missing", "mismatch", "all_wrong"])
def test_missing_or_unadmitted_worker_identity_fails_closed(group, tmp_path, monkeypatch, identity_case):
    actor(monkeypatch, identity_case=identity_case)
    result = run_group(group, tmp_path)
    assert result.get("invalid", {}).get("stage") == "worker runtime identity", result
    assert not result.get("verdict"), result
    assert not result.get("harness_runtime", {}).get("bun")
    assert result["runtime_services"]["workers"]["cleanup"]["complete"]
    assert Path(result["worker_group"]["archive"], "workers.json").is_file()


def test_failed_bun_worker_preserves_execution_failure_and_peer_receipt(group, tmp_path, monkeypatch):
    actor(monkeypatch, failure=True)
    result = run_group(group, tmp_path)
    assert result["invalid"]["stage"] == "worker execution"
    assert result["worker_group"]["workers"]["bob"]["invalid"]["stage"] == "worker launch"
    assert result["worker_group"]["workers"]["alice"]["harness_runtime"]["bun"] == group[1]
    assert not result.get("harness_runtime", {}).get("bun")
    assert result["runtime_services"]["workers"]["cleanup"]["complete"]


def test_bun_workers_cannot_hide_mailbox_failure(group, tmp_path, monkeypatch):
    actor(monkeypatch)
    original_stop = cooperative.Relay.stop

    def stop_with_refusal(relay):
        original_stop(relay)
        relay.receipt["refused"] = 1

    monkeypatch.setattr(cooperative.Relay, "stop", stop_with_refusal)
    result = run_group(group, tmp_path)
    assert result["invalid"]["stage"] == "worker mailbox"
    assert "verdict" not in result
    assert not result.get("harness_runtime")
    assert result["worker_group"]["workers"]["alice"]["harness_runtime"]["bun"] == group[1]
    assert result["runtime_services"]["workers"]["cleanup"]["complete"]


def test_evaluate_accepts_real_bun_worker_aggregate_and_diagnostics(group, monkeypatch):
    actor(monkeypatch, usage=opencode_worker_usage())
    repo, expected, setup, verifier = group
    result = runner._evaluate(repo, [{"task_id": "pair"}], {}, setups={"pair": setup},
                              verifiers={"pair": verifier}, run_id="worker-identity")
    assert not result["invalid"], result
    assert result["per_task"]["pair"] == 1
    assert result["tokens"]["input"] == 32
    assert result["tokens"]["output"] == 8
    assert result["tokens"]["tasks_reported"] == 1
    assert result["harness_runtime"] == [{"bun": expected}]
    summary = result["runtime_diagnostics"]["pair"]["workers"]["worker_group"]
    assert summary["workers"]["bob"]["harness_runtime"]["bun"] == expected


def opencode_worker_usage():
    from base_harness.opencode_agent.agent import EventCollector
    collector = EventCollector(lambda record: None)
    collector.session_tokens = {"input": 10, "output": 3, "reasoning": 1,
                                "cache": {"read": 4, "write": 2}}
    return {name: collector.usage() for name in ("alice", "bob")}


@pytest.mark.parametrize("missing_input", [False, True])
def test_worker_tokens_survive_unknown_call_counts(group, tmp_path, monkeypatch, missing_input):
    from eval.observe.tracing import _last_usage
    usage = opencode_worker_usage()
    if missing_input:
        usage["bob"]["input_tokens"] = None
    actor(monkeypatch, usage=usage)
    result = run_group(group, tmp_path)
    assert result["verdict"]["score"] == 1
    totals = _last_usage(result["trace"])
    assert totals is not None
    assert totals["output_tokens"] == 8
    if missing_input:
        assert "input_tokens" not in totals
    else:
        assert totals["input_tokens"] == 32
    assert "calls" not in totals
    assert result["worker_group"]["workers"]["bob"]["usage"] == usage["bob"]


def test_non_bun_workers_keep_existing_success_path(group, tmp_path, monkeypatch):
    repo, _, _, _ = group
    manifest = json.loads((repo / "harness.json").read_text())
    manifest.pop("runtime")
    (repo / "harness.json").write_text(json.dumps(manifest))
    actor(monkeypatch, bun=False)
    result = run_group(group, tmp_path)
    assert not result.get("invalid"), result
    assert result["verdict"]["score"] == 1, result["verdict"].get("unmeasured")
    assert "harness_runtime" not in result


def test_observed_bun_workers_remain_unsupported(group, tmp_path, monkeypatch):
    group[2]["workers"][0]["env"] = {"kind": "exec", "backend": "observed-v1",
        "image": "sha256:" + "a" * 64,
        "filesystems": {"/workspace": {"bytes": 4096, "inodes": 16}}}
    actor(monkeypatch)
    result = run_group(group, tmp_path)
    assert result["invalid"]["stage"] == "workers admission"
    assert "not supported" in result["invalid"]["detail"]




def test_worker_runtime_receipt_survives_progress_and_resume(group, tmp_path, monkeypatch):
    from eval.observe.tracing import _write_progress
    from harnessgrad.loops.eval_side import _merge_prior, _resume_runtime_mismatch
    actor(monkeypatch)
    result = run_group(group, tmp_path)
    assert result["verdict"]["score"] == 1
    _write_progress(tmp_path, 0, "pair", [result], None)
    record = json.loads((tmp_path / "progress/round-0/pair.json").read_text())
    assert _resume_runtime_mismatch(group[0], {"pair": record}) is None
    merged = _merge_prior({}, {"pair": record})
    assert merged["harness_runtime"] == [{"bun": group[1]}]
    assert merged["runtime_diagnostics"]["pair"]["workers"]["worker_group"]["workers"]["alice"]["harness_runtime"]["bun"] == group[1]
