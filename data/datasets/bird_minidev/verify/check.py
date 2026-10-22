#!/usr/bin/env python3
"""BIRD Mini-Dev's official **EX**, with "cannot measure" kept apart from "wrong".

Provenance: `bird-bench/mini_dev@abd11b6db92a1c9f809b32f7564c7c71b34d67f0`,
`evaluation/evaluation_ex.py`. `calculate_ex` (lines 18-22) is transcribed **verbatim** below
and the test asserts it is a substring of this file, so drift in upstream is a test failure
rather than a slow divergence. The execution semantics around it are upstream's too --
predicted SQL first, then the gold, `fetchall()` each, set equality, and a timeout or an
error on the *prediction* scoring 0 (`evaluation_ex.py::execute_model`, lines 25-43, which
catches `FunctionTimedOut` and `Exception` and returns `res = 0`).

**One deliberate difference**, and it is why this file is not `execute_model` wholesale:
upstream catches everything in one `try`, so a **gold** query that fails -- a broken
database, a missing table -- is scored as a wrong answer. This platform treats that as
unmeasurable and writes `measured: false`, because a harness's 0 and an environment's 0 are
otherwise indistinguishable (`docs/dataset-onboarding-skill.md` §5, and the same rule
`data/scaffolds/qa/verify/check.py` implements by exiting 3). A missing or empty `answer.sql` is *not*
in that class: the task ran and the agent did not answer, which is a measured zero.

The reward is per-question EX. The benchmark's other two official columns -- Soft-F1
(`evaluation_f1.py`) and R-VES (`evaluation_ves.py`) -- are separate metrics over the same
query pair and are **not** reported here; the metric is named in the evidence so a published
number cannot be mistaken for one of them.
"""
from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
from pathlib import Path

# Resolve helpers beside trusted scorer code. The pinned route places this directory
# outside the actor task tree, so task-local Python files cannot shadow imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data.scaffolds.scorer_input import add_arguments, run_bound

#: Upstream's per-query budget (`evaluation_ex.py`, `--meta_time_out` default 30.0).
TIMEOUT_S = 30.0


# --- verbatim: bird-bench/mini_dev@abd11b6 evaluation/evaluation_ex.py lines 18-22 ---------
def calculate_ex(predicted_res, ground_truth_res):
    res = 0
    if set(predicted_res) == set(ground_truth_res):
        res = 1
    return res
# -------------------------------------------------------------------------------------------


class QueryTimedOut(Exception):
    """The query exceeded upstream's 30-second budget."""


def _alarm(_signum, _frame):
    raise QueryTimedOut(f"exceeded {TIMEOUT_S:g}s")


def run_query(connection: sqlite3.Connection, sql: str) -> list[tuple]:
    """`execute_sql`'s two lines, with upstream's timeout as a real interrupt."""
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT_S)
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        return cursor.fetchall()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _write(path: str, payload: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")


def _measure(args, reward: float, why: str) -> int:
    _write(args.reward_file, f"{reward:g}\n")
    _write(args.evidence_file, json.dumps(
        {"scorer_input": args.scorer_input_receipt, "measured": True, "metric": "EX", "reward": reward, "why": why}, sort_keys=True) + "\n")
    print(f"reward {reward:g}: {why}")
    return 0


def _cannot_measure(args, why: str) -> int:
    """No reward file at all: `eval/verification/verifier.py` turns this into an `invalid` task."""
    _write(args.evidence_file, json.dumps(
        {"scorer_input": args.scorer_input_receipt, "measured": False, "metric": "EX", "error": why}, sort_keys=True) + "\n")
    print(f"check cannot measure: {why}", file=sys.stderr)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--pred", required=True)
    parser.add_argument("--reward-file", default="verifier/reward.txt")
    parser.add_argument("--evidence-file", default="verifier/evidence.json")
    add_arguments(parser)
    args = parser.parse_args()
    return run_bound(args, {args.gold: Path(args.gold), args.db: Path(args.db)},
                     lambda inputs, receipt: _score(args, inputs, receipt))


def _score(args, inputs, receipt) -> int:

    args.scorer_input_receipt = receipt
    gold_sql = inputs[args.gold].read_text(encoding="utf-8").strip() if inputs[args.gold].is_file() else ""
    if not Path(args.db).is_file():
        return _cannot_measure(args, f"no database at {args.db}: this task cannot be graded")
    if not gold_sql:
        return _cannot_measure(args, "the gold query is empty: this task cannot be graded")

    connection = sqlite3.connect(str(inputs[args.db]))
    try:
        try:
            gold_rows = run_query(connection, gold_sql)
        except Exception as exc:                    # noqa: BLE001 - a broken gold is not the agent's
            return _cannot_measure(
                args, f"the gold query cannot run against {Path(args.db).name} "
                      f"({type(exc).__name__}: {exc}): the environment cannot measure this task")

        prediction = (Path(args.pred).read_text(encoding="utf-8").strip()
                      if Path(args.pred).is_file() else "")
        if not prediction:
            return _measure(args, 0.0, "no answer (measured zero): answer.sql is missing or empty")
        try:
            predicted_rows = run_query(connection, prediction)
        except Exception as exc:                    # noqa: BLE001 - upstream scores this 0
            return _measure(args, 0.0, f"prediction did not run ({type(exc).__name__}: {exc})")
    finally:
        connection.close()

    reward = float(calculate_ex(predicted_rows, gold_rows))
    return _measure(args, reward, f"exact set equality: {len(predicted_rows)} predicted vs "
                                  f"{len(gold_rows)} gold rows")


if __name__ == "__main__":
    raise SystemExit(main())
