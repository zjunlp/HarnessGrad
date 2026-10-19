"""`--resume` on the eval side: skip what was measured, refuse what does not fit.

The helpers are unit-tested rather than the whole driver because the failure that matters
is arithmetic: a resumed point must describe one measurement of one pool, and a merged
`per_task` that loses or duplicates a task is exactly the silent corruption a resume is
risky for.
"""

from __future__ import annotations

import json
from pathlib import Path

from harnessgrad.loops.eval_side import (
    _merge_prior,
    _progress_results,
    _resume_mismatch,
)


def _progress(run_dir: Path, task_id: str, *, score=None, outcome="completed",
              complete=True, detail="") -> None:
    d = run_dir / "progress" / "round-0"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps({
        "task_id": task_id, "round": 0, "complete": complete,
        "process_outcome": outcome, "score": score, "detail": detail}))


def _meta(run_dir: Path, **fields) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_meta.json").write_text(json.dumps(fields))


def test_progress_results_reads_only_complete_records(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _progress(run_dir, "a", score=1.0)
    _progress(run_dir, "b", score=0.0)
    _progress(run_dir, "c", score=None, complete=False)
    got = _progress_results(run_dir)
    assert set(got) == {"a", "b"}, "an incomplete task must not count as a result"


def test_merge_keeps_every_task_exactly_once(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _progress(run_dir, "a", score=1.0)
    _progress(run_dir, "b", score=0.0)
    _progress(run_dir, "c", outcome="invalid", detail="env died")
    prior = _progress_results(run_dir)
    fresh = {"per_task": {"d": 1.0}, "invalid": [], "harness_failed": []}
    merged = _merge_prior(fresh, prior)
    assert merged["per_task"] == {"d": 1.0, "a": 1.0, "b": 0.0}
    assert [x["task_id"] for x in merged["invalid"]] == ["c"]
    # a task that the fresh half already scored must not be overwritten by the old record
    assert "a" not in fresh["per_task"]


def test_merge_does_not_duplicate_an_overlap(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _progress(run_dir, "a", score=1.0)
    prior = _progress_results(run_dir)
    merged = _merge_prior({"per_task": {"a": 0.0}, "invalid": [], "harness_failed": []}, prior)
    assert merged["per_task"] == {"a": 0.0}, "the fresh measurement wins, not a duplicate key"


def test_resume_refuses_a_different_state_or_pool(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _progress(run_dir, "a", score=1.0)
    _meta(run_dir, evaluated_from={"run_id": "rsi10-x-train", "round": 3},
          env={"pool": "valid_unseen"})

    class Args:
        from_run = "rsi10-x-train"
        from_round = 3

    assert _resume_mismatch(run_dir, Args(), "valid_unseen", "sha") is None

    Args.from_round = 1
    assert "round" in (_resume_mismatch(run_dir, Args(), "valid_unseen", "sha") or "")
    Args.from_round = 3

    assert "pool" in (_resume_mismatch(run_dir, Args(), "valid_seen", "sha") or "")
    Args.from_run = "someone-else"
    assert "from-run" in (_resume_mismatch(run_dir, Args(), "valid_unseen", "sha") or "")


def test_resume_refuses_without_run_meta(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _progress(run_dir, "a", score=1.0)

    class Args:
        from_run = "x"
        from_round = 0

    why = _resume_mismatch(run_dir, Args(), "valid_unseen", "sha")
    assert why and "run_meta" in why


def test_resume_refuses_when_evaluated_from_is_absent(tmp_path: Path) -> None:
    """A run that records nothing about what it measured cannot be resumed safely."""
    run_dir = tmp_path / "run"
    _progress(run_dir, "a", score=1.0)
    _meta(run_dir, env={"pool": "valid_unseen"})     # no evaluated_from

    class Args:
        from_run = "rsi10-x-train"
        from_round = 3

    why = _resume_mismatch(run_dir, Args(), "valid_unseen", "sha")
    assert why and "evaluated_from" in why
