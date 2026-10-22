"""A verifier timeout is a score of 0, not an exclusion -- and nothing else moves.

Operator decision, 2026-10-23. These tests pin the selector (`outcome == "timeout"`) and the two
places that have to agree with it: the runner's classification and the point's fields.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.run.runner import timeout_verdict          # noqa: E402
from harnessgrad.records import _curve_point          # noqa: E402


def test_the_verifiers_own_timeout_verdict_is_a_score():
    reason = {"stage": "check", "detail": "the verifier timed out after 900s", "outcome": "timeout"}
    assert timeout_verdict(reason) is True


def test_the_process_level_timeout_string_is_a_score():
    """`repeated_trials.result_row` reports a budget kill as the bare string `"timeout"`."""
    assert timeout_verdict("timeout") is True


def test_platform_failures_stay_unmeasured():
    """The three verdicts that are *not* the harness's fault must not become zeros."""
    assert timeout_verdict({"stage": "check", "outcome": "unmeasured",
                            "detail": "the check wrote no /verifier/reward.txt"}) is False
    assert timeout_verdict("relayed_signal") is False
    assert timeout_verdict("stopped_by_signal") is False
    assert timeout_verdict(None) is False


def test_the_point_keeps_the_reason_and_counts_the_zero():
    """`n_scored` covers the zero, `n_timeout` says why, and `invalid` does not double-count it."""
    point = _curve_point(
        run_id="probe", round_index=0,
        identity={"harness_name": "loop", "harness_version": "0.2.0", "harness_sha": "x"},
        sampling={"dataset": "appworld", "policy": "all", "task_ids": ["a", "b"]},
        scores=[1.0, 0.0], task_ids=["a", "b"],
        cost={}, cumulative={},
        invalid={}, harness_failed={},
        timeouts={"b": {"stage": "check", "detail": "the verifier timed out after 900s",
                        "outcome": "timeout"}},
    )
    assert point["n_scored"] == 2
    assert point["n_timeout"] == 1
    assert point["timeouts"]["b"]["outcome"] == "timeout"
    assert point["n_invalid"] == 0
    assert point["score"] == 0.5          # the timeout is in the denominator
    assert point["n_passed"] == 1


def test_a_point_without_timeouts_has_no_timeout_field():
    """Absent, not `{}`: a point that predates the rule must not read as "measured, no timeouts"."""
    point = _curve_point(
        run_id="probe", round_index=0,
        identity={"harness_name": "loop", "harness_version": "0.2.0", "harness_sha": "x"},
        sampling={"dataset": "appworld", "policy": "all", "task_ids": ["a"]},
        scores=[1.0], task_ids=["a"], cost={}, cumulative={}, invalid={}, harness_failed={},
    )
    assert point["n_timeout"] == 0
    assert "timeouts" not in point
