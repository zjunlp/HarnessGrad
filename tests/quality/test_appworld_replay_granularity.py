"""Replay trusted API calls in order without imposing the actor's execution cap twice.

The official evaluator grades database state via ModelCollectionPair. However,
AppWorld.execute enforces max_interactions (default 1000) and increments
num_interactions once per code action. One actor action can issue multiple APIs,
so one verifier execute per logged API may exceed that cap on a valid transcript.
Replay therefore provisions one execution per trusted recorded call. It never
executes actor-supplied code or changes the actor service's interaction policy.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "data/datasets/appworld/verify/check.py"

#: A stand-in `appworld` package: records every `execute` call, and `evaluate()` returns a
#: tracker that passes. It exists so this test needs no Docker, no task data and no database.
STUB = textwrap.dedent('''
    import json, pathlib

    RECORD = pathlib.Path(__file__).with_name("executed.json")

    class _Tracker:
        success = True
        num_tests = 1
        passes = [{"label": "stub", "requirement": "stub"}]
        failures = []

    class AppWorld:
        def __init__(self, **kwargs):
            self.calls = []
            self.max_interactions = kwargs.get("max_interactions", 1000)
            RECORD.with_name("kwargs.json").write_text(json.dumps(kwargs))
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            RECORD.write_text(json.dumps(self.calls))
            return False
        def execute(self, code):
            if len(self.calls) >= self.max_interactions:
                return "Maximum number of executions reached."
            self.calls.append(code)
            return ""
        def evaluate(self):
            return _Tracker()
''')


def _run(tmp_path: Path, n_calls: int) -> list[str]:
    stub_dir = tmp_path / "stub" / "appworld"
    stub_dir.mkdir(parents=True)
    (stub_dir / "__init__.py").write_text(STUB, encoding="utf-8")
    log = tmp_path / "api_calls.jsonl"
    log.write_text("".join(
        json.dumps({"url": f"/api/{index}", "method": "get", "data": {"n": index}}) + "\n"
        for index in range(n_calls)), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--task-id", "t_1", "--log", str(log),
         "--reward-file", str(tmp_path / "reward.txt"),
         "--evidence-file", str(tmp_path / "evidence.json")],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path / "stub")},
        capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    executed = json.loads((tmp_path / "stub" / "appworld" / "executed.json").read_text())
    assert (tmp_path / "reward.txt").read_text().strip() == "1.0"
    return executed


def test_one_execute_per_logged_call_in_order(tmp_path):
    executed = _run(tmp_path, 3)
    assert len(executed) == 3
    for index, code in enumerate(executed):
        assert f"/api/{index}" in code, executed


def test_the_replayed_url_drops_the_query_string(tmp_path):
    """`/api/0?x=1` and `/api/0` are the same endpoint; the log may carry the query."""
    stub_dir = tmp_path / "stub" / "appworld"
    stub_dir.mkdir(parents=True)
    (stub_dir / "__init__.py").write_text(STUB, encoding="utf-8")
    log = tmp_path / "api_calls.jsonl"
    log.write_text(json.dumps({"url": "/api/0?page=2", "method": "get", "data": {}}) + "\n",
                   encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--task-id", "t_1", "--log", str(log),
         "--reward-file", str(tmp_path / "reward.txt"),
         "--evidence-file", str(tmp_path / "evidence.json")],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path / "stub")},
        capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    executed = json.loads((stub_dir / "executed.json").read_text())
    assert executed and "'/api/0'" in executed[0] and "page=2" not in executed[0]


def test_replay_allows_one_execution_per_logged_api_call(tmp_path):
    # A single action with a loop can emit more than the default 1000 APIs.
    # The stand-in mirrors upstream execute's cap and silently rejects excess
    # executions, so assert actual replay, not just the constructor argument.
    executed = _run(tmp_path, 1002)
    assert len(executed) == 1002
    kwargs = json.loads((tmp_path / "stub/appworld/kwargs.json").read_text())
    assert kwargs["max_interactions"] == 1002


def test_the_evidence_carries_no_execution_counter(tmp_path):
    """The graded evidence is the tracker's tests, not a count of how many calls ran."""
    _run(tmp_path, 2)
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert set(evidence) == {"task_id", "source_split", "metric", "metric_name", "measured",
                             "success", "num_tests", "passes", "failures"}, sorted(evidence)
