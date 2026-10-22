"""A trace line that merely mentions `wall_clock_s` must not kill the run.

Measured on 2026-10-17: `dream_rsi`'s ninth round ended with `KeyError: 'wall_clock_s'`
because a *step* record carried the text in its output; the writer matched on the substring,
parsed that record, and indexed a key it did not have. The train returned non-zero, and the
launcher — which returns before the exam when a train fails — never measured its kept round.
One loose substring test cost a nine-round run and its exam.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.observe.tracing import _write_progress


def _measured(trace_lines: list[str]) -> list[dict]:
    return [{"trace": "\n".join(trace_lines) + "\n", "verdict": {"passed": True},
             "scores": [1.0], "exit_code": 0, "stderr": ""}]


def test_a_step_record_that_mentions_the_key_is_skipped(tmp_path: Path) -> None:
    """The crash case: the first matching line is a step, not the summary."""
    trace = [
        # A *nested* `wall_clock_s`: the line carries the raw key, parses as a dict, and
        # has no such top-level key. That is the shape that raised in production.
        json.dumps({"summary": {"wall_clock_s": {"total": 1.0}}}),
        json.dumps({"wall_clock_s": {"model": 1.0, "commands": 2.0, "total": 3.0}}),
    ]
    _write_progress(tmp_path, 0, "t1", _measured(trace), None)
    record = json.loads((tmp_path / "progress" / "round-0" / "t1.json").read_text())
    assert record["wall_clock_s"] == 3.0


def test_a_trace_with_no_summary_writes_no_wall_clock(tmp_path: Path) -> None:
    trace = [json.dumps({"wall_clock_s": "not a mapping"})]
    _write_progress(tmp_path, 0, "t1", _measured(trace), None)
    record = json.loads((tmp_path / "progress" / "round-0" / "t1.json").read_text())
    assert record["wall_clock_s"] is None


def test_truncated_json_on_a_matching_line_is_ignored(tmp_path: Path) -> None:
    """A killed harness leaves a half-written line; it must not raise either."""
    trace = ['{"wall_clock_s": {"total": 2.0}', json.dumps({"wall_clock_s": {"total": 4.0}})]
    _write_progress(tmp_path, 0, "t1", _measured(trace), None)
    record = json.loads((tmp_path / "progress" / "round-0" / "t1.json").read_text())
    assert record["wall_clock_s"] == 4.0
