"""Post-record completion and derived reporting, with separate train/eval paths.

Curve/budget/resume publication remains the caller's responsibility. Completion
classification and summary rendering each retain their canonical implementation.
Only the existing render/write Exception boundary is soft; cancellation escapes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Protocol

import tools.run_summary as run_summary
from harnessgrad.completion import describe
from harnessgrad.records import _write_run_meta


@dataclass(frozen=True)
class SummaryOptions:
    no_summary: bool = False
    directory: str | Path | None = None
    check_only: bool = False
    prepare_only: bool = False


@dataclass(frozen=True)
class TrainFinishOptions:
    requested_rounds: int
    fail_on_incomplete: bool
    summary: SummaryOptions


@dataclass(frozen=True)
class EvalFinishOptions:
    fail_on_incomplete: bool
    summary: SummaryOptions


class Reporter(Protocol):
    def note(self, message: str, *, level: str): ...
    def results(self, curve, *, split): ...
    def artifact(self, label: str, path: Path): ...


class SummaryWriteNotices:
    """Where `run_summary.write_summary`'s own notices go when the driver writes the page.

    A finished run announces the page with `console.artifact`, beside `curve`, `workspace`
    and `events`, so a fresh write's "wrote <path>" line would print the same fact twice.
    Everything else is forwarded to stderr: `tools/run_summary.py` promises that replacing
    a *different* earlier summary is never silent, and finishing an earlier run with
    `--resume` is exactly the path that can hit it.
    """

    def __init__(self) -> None:
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if not line.startswith("wrote "):
                print(line, file=sys.stderr)
        return len(text)

    def flush(self) -> None:
        if self._pending:
            line, self._pending = self._pending, ""
            if not line.startswith("wrote "):
                print(line, file=sys.stderr)


def write_end_summary(options: SummaryOptions, run_dir: Path, curve_path: Path) -> Path | None:
    if options.no_summary:
        return None
    if options.check_only or options.prepare_only:
        return None
    if not curve_path.is_file():
        return None
    target = Path(options.directory) if options.directory else run_dir
    try:
        summary = run_summary.summarise(run_dir)
        run_summary.write_summary(summary, target, dry_run=False, stream=SummaryWriteNotices())
    except Exception as exc:
        print(f"warning: end-summary not written to {target}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    return target / run_summary.SUMMARY_MD


def finish_train(options: TrainFinishOptions, run_dir: Path, curve_path: Path, curve, split,
                 work: Path, reporter: Reporter) -> int:
    metadata = json.loads((run_dir / 'run_meta.json').read_text())
    completion = describe(curve, requested_rounds=options.requested_rounds,
        failed_steps=metadata.get('failed_steps') or [], method_stopped=metadata.get('method_stopped', False))
    _write_run_meta(run_dir, completion=completion, fail_on_incomplete=options.fail_on_incomplete)
    label = completion['status'].upper()
    reporter.note(f"{label}: {completion['completed_rounds']}/{completion['requested_rounds']} "
                  f"improvement rounds; {completion['reason']}",
                  level='warn' if completion['status'] == 'incomplete' else 'info')
    summary_path = write_end_summary(options.summary, run_dir, curve_path)
    reporter.results(curve, split=split)
    reporter.artifact("curve", curve_path)
    if summary_path is not None:
        reporter.artifact("summary", summary_path)
    reporter.artifact("workspace", work)
    reporter.artifact("events", run_dir / "events.jsonl")
    return 2 if options.fail_on_incomplete and completion['status'] == 'incomplete' else 0


def finish_eval(options: EvalFinishOptions, run_dir: Path, result, reporter: Reporter):
    curve_path = run_dir / 'curve.jsonl'
    if result == 0 and curve_path.is_file():
        points = [json.loads(line) for line in curve_path.read_text().splitlines() if line.strip()]
        completion = describe(points, requested_rounds=0)
    else:
        completion = {'status': 'incomplete', 'requested_rounds': 0, 'completed_rounds': 0,
            'reason': 'held-out measurement did not complete', 'failed_rounds': [], 'coverage_complete': False}
    _write_run_meta(run_dir, completion=completion, fail_on_incomplete=options.fail_on_incomplete)
    reporter.note(f"{completion['status'].upper()}: held-out measurement; {completion['reason']}",
                  level='warn' if completion['status'] == 'incomplete' else 'info')
    if result == 0:
        write_end_summary(options.summary, run_dir, curve_path)
    if options.fail_on_incomplete and completion['status'] == 'incomplete':
        return 2
    return result
