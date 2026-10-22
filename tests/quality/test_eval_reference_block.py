"""The exam point says what its frozen reference is, and why it may not describe it.

`mode_a` has written `baseline.problems` since the frozen-reference protocol existed, and the
eval side -- the side whose entire purpose is to be compared against that reference -- wrote
nothing at all. Measured 2026-10-18: all eleven published `exam` points carry no `baseline`
block, so the one fact a reader could not see is that every frozen reference was measured at
`jobs=13` while those exams ran at `jobs=12`-`40`.

`jobs` is exactly the field `policies.REFERENCE_MEASUREMENT` keeps visible because contention
does not show up in a score: a number measured while 13 tasks shared the machine is not the
same measurement as one taken with 40, even when the harness is identical.
"""

from __future__ import annotations

import json
from pathlib import Path

from harnessgrad.loops.eval_side import _baseline_block


def _point(*, jobs: int = 40, tasks: tuple[str, ...] = ("t1", "t2", "t3")) -> dict:
    return {
        "run_id": "rsi10-test",
        "round": 0,
        "identity": {"jobs": jobs, "trials": 1, "agent_model": "qwen3.5-9b-local",
                     "agent_backend": "vllm", "harness_name": "loop",
                     "framework_version": "1.0"},
        "per_task": {task: 0.0 for task in tasks},
    }


def _reference(tmp_path: Path, *, jobs: int = 13,
               tasks: tuple[str, ...] = ("t1", "t2", "t3")) -> Path:
    path = tmp_path / "reference.json"
    path.write_text(json.dumps({
        "run_id": "reference-run", "round": 0,
        "identity": {"jobs": jobs, "trials": 1, "agent_model": "qwen3.5-9b-local",
                     "agent_backend": "vllm", "harness_name": "loop",
                     "framework_version": "1.0"},
        "per_task": {task: 0.0 for task in tasks},
    }))
    return path


def test_the_contention_mismatch_is_recorded_rather_than_silent(tmp_path: Path) -> None:
    block = _baseline_block(str(_reference(tmp_path)), _point(jobs=40))
    assert block["kind"] == "frozen"
    assert block["run_id"] == "reference-run"
    assert any("jobs changed" in problem and "13" in problem and "40" in problem
               for problem in block["problems"]), (
        "参考点 jobs=13 与本次 jobs=40 是读者必须知道的事实，不能只活在报告里")


def test_a_matching_reference_has_no_problems(tmp_path: Path) -> None:
    block = _baseline_block(str(_reference(tmp_path)), _point(jobs=13))
    assert block["problems"] == []


def test_a_task_set_that_moved_is_named(tmp_path: Path) -> None:
    """题集不同会让配对检验悄悄缩小到交集上 —— 这个字段就是为了不悄悄发生。"""
    block = _baseline_block(str(_reference(tmp_path)), _point(jobs=13, tasks=("t1", "t2")))
    assert any("task set changed" in problem for problem in block["problems"])


def test_an_unreadable_reference_is_a_problem_not_a_lost_measurement(
        tmp_path: Path) -> None:
    """考试已经测完，分数是真的；作废的应该是比较，不是这一轮。"""
    block = _baseline_block(str(tmp_path / "missing.json"), _point())
    assert block["kind"] == "frozen"
    assert block["problems"] and "could not be read" in block["problems"][0]
