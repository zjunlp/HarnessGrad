"""The AppWorld replay must be exactly one `world.execute` per logged API call, in order.

Why this test exists: a reviewer claimed the scoring counts executions at the wrong
granularity -- "one agent action may dispatch several API calls, but scoring records one
execution per API call, so a correct solution can be judged wrong". Two things settle it:

  * the official evaluator grades the **database state** before and after the episode
    (`appworld/evaluator.py: evaluate_task` builds a `ModelCollectionPair` and runs the
    task's evaluation module against it); nothing in the package or in the task data reads an
    execution counter, and `num_executions` does not exist anywhere in it;
  * empirically, `data/datasets/appworld/control.py --oracle` runs the reference solution
    live, then replays its `api_calls.jsonl` through `verify/check.py`: two tasks measured
    TGC 1.0 with every expected test passing, so replay does not misjudge a correct solution.

What remains true, and is what this test pins, is the **contract**: the verifier replays one
`execute` per log line. If a future evaluator ever graded on execution counts, this test is
where the assumption lives -- and changing it would be a deliberate act, not an accident.
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
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            RECORD.write_text(json.dumps(self.calls))
            return False
        def execute(self, code):
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


def test_the_evidence_carries_no_execution_counter(tmp_path):
    """The graded evidence is the tracker's tests, not a count of how many calls ran."""
    _run(tmp_path, 2)
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert set(evidence) == {"task_id", "source_split", "metric", "metric_name", "measured",
                             "success", "num_tests", "passes", "failures"}, sorted(evidence)
