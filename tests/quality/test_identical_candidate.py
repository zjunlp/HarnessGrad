"""An identical candidate must cost a point, not a measurement.

The case this exists for: a method with no early stop reaches the ceiling, has nothing
left to change, and emits "no edit" for its remaining rounds. Re-running the whole task
set to learn a number the record already holds is the most expensive way to record "no
change" -- but the round still has to be written, because the round *happened* and the
method's budget moves.

These tests pin the two halves that can be wrong silently: matching the wrong state
(different content or a different trial count), and losing the traces the reused round
should carry.
"""

from __future__ import annotations

import json
from pathlib import Path

from harnessgrad.loops.mode_a import _copy_round_traces, _identical_prior


def _point(round_no: int, sha: str, *, trials: int = 1, score: float = 0.5) -> dict:
    return {
        "round": round_no,
        "score": score,
        "identity": {"harness_sha": sha},
        "sampling": {"trials": trials},
        "per_task": {"t1": 1.0, "t2": 0.0},
    }


def test_matches_the_same_content() -> None:
    curve = [_point(0, "aaaa"), _point(1, "bbbb")]
    got = _identical_prior(curve, "aaaa", 1)
    assert got is not None and got["round"] == 0


def test_returns_none_for_new_content() -> None:
    curve = [_point(0, "aaaa")]
    assert _identical_prior(curve, "cccc", 1) is None


def test_a_different_trial_count_is_a_different_question() -> None:
    curve = [_point(0, "aaaa", trials=3)]
    assert _identical_prior(curve, "aaaa", 1) is None
    assert _identical_prior(curve, "aaaa", 3) is not None


def test_prefers_the_most_recent_match() -> None:
    curve = [_point(0, "aaaa"), _point(1, "aaaa"), _point(2, "bbbb")]
    got = _identical_prior(curve, "aaaa", 1)
    assert got is not None and got["round"] == 1


def test_the_reused_round_keeps_the_earlier_traces(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    src = run_dir / "round0_traces"
    src.mkdir(parents=True)
    (src / "t1.jsonl").write_text(json.dumps({"step": 0}) + "\n")

    _copy_round_traces(run_dir, 0, 3)
    copied = run_dir / "round3_traces" / "t1.jsonl"
    assert copied.is_file(), "a reused round with no traces reads as a round that produced none"


def test_copy_never_clobbers_an_existing_round(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    src = run_dir / "round0_traces"
    src.mkdir(parents=True)
    (src / "t1.jsonl").write_text("original\n")
    dst = run_dir / "round3_traces"
    dst.mkdir()
    (dst / "t1.jsonl").write_text("this round's own\n")

    _copy_round_traces(run_dir, 0, 3)
    assert (dst / "t1.jsonl").read_text() == "this round's own\n"
