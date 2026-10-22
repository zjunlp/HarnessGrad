"""A launch that never happened must say so, and must not borrow a signal's name.

`eval/run/runner.py` writes `exit_code = -1` for two unrelated events: the platform's own
`agent_timeout_s` expiry, which carries a `timeout after Ns` stderr marker, and a stepwise
harness that never launched, which carried no marker at all. `process_outcome` tells the
timeout apart by that marker, so the second became `stopped_by_signal` and the per-task record
then stated, as fact:

    the task was terminated by signal 1 (exit code -1) from outside the measurement

Nothing was terminated and nothing sent a signal - the harness never started. The outcome was
already right (unmeasured rather than a score); the *reason* was fabricated, which this
repository treats as its own defect regardless of the outcome. `w-outcome` (`59cbf662`)
introduced the layer that made the wrong reason visible; the parent corrected the branch after
finding it, and this file keeps it corrected.

The fix is at the **producer**, not the classifier. `process_outcome` cannot tell a bare `-1`
apart from SIGHUP - that ambiguity is inherent to the value - so the branch stops producing a
bare `-1` and returns the platform's own invalid result, exactly as the two `OSError` branches
directly above it already did.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.observe import tracing
from eval.run import runner


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "agent.py"}))
    (repo / "agent.py").write_text("pass")
    return repo


class _FakeRelay:
    """Enough of `StepwiseRelay` for the launch path and its `finally`."""

    path = Path("/tmp/hg-launch-failure-probe.sock")

    def start(self):
        return self

    def stop(self):
        pass

    def receipt(self):
        """A relay that never got a connection: what `chess_stepwise.finish` reads.

        The fix returns before `finish` is reached, so this exists to keep the *pre-fix*
        behaviour observable: with it, reverting the branch lets the run proceed to the
        finisher and fail this test's assertion instead of failing on a missing method.
        """
        return {"schema": "harnessgrad.unix-stepwise-relay.v1", "protocol": "stepwise",
                "status": "incomplete", "measured": False, "production_authorized": False,
                "limits": {"timeout_s": 1, "max_connections": 1, "max_submissions": 1,
                           "max_message_bytes": 1024},
                "connections": 0, "rejections": [], "rejected_connections": 0,
                "transport": {"status": "incomplete", "reason": "no_connection", "events": []},
                "cleanup": {"server_closed": True, "thread_joined": True}}


def test_a_stepwise_launch_failure_is_invalid_and_not_a_signal(tmp_path, monkeypatch):
    """The branch returns `_invalid`, so no `-1` sentinel and no signal claim exists."""
    repo = _repo(tmp_path)
    monkeypatch.setattr(runner.chess_stepwise, "session_for", lambda *a, **k: object())
    monkeypatch.setattr(runner, "StepwiseRelay", lambda *a, **k: _FakeRelay())

    def boom(*_a, **_k):
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(runner, "run_process", boom)

    result = runner.run_one(repo, {"task_id": "synthetic"}, sandbox=False,
                            env={"kind": "files"})

    assert result["invalid"]["stage"] == "harness launch"
    assert "stepwise harness launch failed" in result["invalid"]["detail"]
    assert "signal" not in result["invalid"]["detail"].lower()
    # The whole point: the producer no longer emits the sentinel the classifier misreads.
    assert result["exit_code"] is None, result["exit_code"]


def test_the_budget_expiry_is_still_the_platforms_own_negative_one():
    """The correction must not have blurred the one `-1` that *is* the platform's."""
    assert tracing.process_outcome(-1, "timeout after 900s") == "platform_timeout"
    assert tracing.process_outcome(-1, "") == "stopped_by_signal"


def test_the_classifier_cannot_separate_a_bare_negative_one_from_sighup():
    """Why the fix is at the producer: the ambiguity is inherent to the value.

    SIGHUP is signal 1, so a bare `-1` is *also* a legitimate signal code, and the stderr text
    of a launch failure is not a marker the classifier was given. No classifier can separate
    the two, which is why the sentinel had to stop being produced rather than be taught apart.
    """
    assert tracing.process_outcome(-1, "stepwise harness launch failed") == "stopped_by_signal"
    assert tracing.process_outcome(-1, "") == "stopped_by_signal"
    assert tracing.process_outcome(-15, "stepwise harness launch failed") == "stopped_by_signal"
