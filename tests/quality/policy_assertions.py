"""Exact assertions for the fail-closed runner contract (008348b).

These assertions never grant admission or turn an invalid task into a score.
"""
import json


#: The fields an exec declaration is refused for. `env.agent_timeout_s` and
#: `env.verify_timeout_s` are deliberately absent: they flow through `phase_timeout`
#: to the platform's own subprocess deadlines, which this process applies, so they are
#: not evidence an external backend must supply (`eval/run/runner.py:41-72`, corrected
#: by `w-jerichoadm`). `env.network` is present because these fixtures declare
#: `kind="exec"`; a native files declaration is refused for network alone.
EXEC_POLICY_FIELDS = (
    "env.cpus", "env.exec_defaults", "env.memory_mb", "env.network",
)


def assert_invalid_policy(invalid, fields):
    assert invalid == {
        "stage": "admission", "service": None,
        "detail": "backend_observation_unavailable",
        "receipt": {
            "schema": "harnessgrad.runner-policy-refusal.v1",
            "stage": "admission", "status": "blocked",
            "reason": "backend_observation_unavailable",
            "measured": False, "production_authorized": False,
            "observation": "unknown", "policy_fields": sorted(fields),
        },
    }


def assert_policy_refusal(result, fields):
    assert_invalid_policy(result["invalid"], fields)
    assert result["exit_code"] is None
    assert result["answer"] == result["trace"] == result["stderr"] == ""
    assert result["model_gateway"] is None
    assert result["identity"] is None and result["env_usage"] == {}
    assert "score" not in result and "verdict" not in result


def assert_driver_policy_refusal(proc, tmp_path, task_ids, fields=EXEC_POLICY_FIELDS):
    said = proc.stdout + proc.stderr
    assert proc.returncode == 2, said
    assert "backend_observation_unavailable" in said, said
    run = tmp_path / "runs/probe"
    progress = run / "progress/round-0"
    assert {p.stem for p in progress.glob("*.json")} == set(task_ids)
    for task_id in task_ids:
        record = json.loads((progress / (task_id + ".json")).read_text())
        assert record["task_id"] == task_id and record["round"] == 0
        assert_invalid_policy(record["invalid"], fields)
        assert record["harness_output"] == {
            "exit_code": None, "stderr": "", "harness_runtime": None,
            "model_gateway": None,
        }
    assert not list((progress / "traces").glob("*"))
    curve = run / "curve.jsonl"
    assert not curve.exists() or not curve.read_text().strip()
    assert not list((run / "recordings").rglob("*"))
