#!/usr/bin/env python3
"""GAIA's official scorer, **called** rather than transcribed.

`scorer.py` arrives as a `VERIFY.inputs` entry (`dst: /scorer.py`) copied out of `_src/`, where it
was pinned by sha256 from the official leaderboard Space. This script therefore runs upstream's
own `question_scorer` -- the same call the leaderboard makes (`app.py`:
`question_scorer(task['model_answer'], gold_results[...]["Final answer"])`) -- and adds only the
platform's reward file, the placeholder guard and the evidence.

Why calling matters here more than usual: the scorer has **three branches that normalize
differently** -- a float (strip `$`, `%`, `,` then convert), a comma/semicolon list (compare
element-wise, keeping punctuation), and a string (remove whitespace, lowercase, strip punctuation).
A hand-written "exact match" agrees with it on the easy third of the pool and silently disagrees on
the rest, which is the failure mode this repository's gate 3 exists to prevent.

Two outcomes that must not look alike
-------------------------------------
* a missing or empty `answer.txt` is a **measured zero** -- the task ran and the agent did not
  answer;
* a gold that is empty or the test split's placeholder `"?"` is **unmeasurable** (`measured: false`,
  exit 3, no reward file). That is not hypothetical: 301 of the release's 466 rows carry `"?"`, and
  a scorer run over them would compare a submission against a placeholder and report a number.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

#: The scorer is copied to `/` beside this file, so the script's own directory is enough.
sys.path.insert(0, str(Path(__file__).resolve().parent))

WORKDIR = Path("/app")
DEFAULT_ANSWER = str(WORKDIR / "answer.txt")


def _write(path: str, payload: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")


def _identity(record: dict) -> dict:
    """The labels the gold record already carries, passed into the evidence unchanged.

    `level` is GAIA's own difficulty label (1/2/3), and this check is the only place that
    knows what a level is: reporting it here is what lets the platform publish accuracy per
    level (`harnessgrad/records.py::per_level_breakdown`) without the platform having to
    learn a dataset's vocabulary. It is **reporting only** -- it never enters `reward`, and
    nothing below branches on it.
    """
    return {"task_id": record.get("task_id"), "level": record.get("level")}


def _measure(args, reward: float, why: str, extra: dict) -> int:
    _write(args.reward_file, f"{reward:g}\n")
    evidence = {"measured": True, "metric": "GAIA exact match (official question_scorer)",
                "reward": reward, "why": why}
    evidence.update(extra)
    _write(args.evidence_file, json.dumps(evidence, sort_keys=True) + "\n")
    print(f"reward {reward:g}: {why}")
    return 0


def _cannot_measure(args, why: str) -> int:
    _write(args.evidence_file, json.dumps(
        {"measured": False, "metric": "GAIA exact match", "error": why}, sort_keys=True) + "\n")
    print(f"check cannot measure: {why}", file=sys.stderr)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer", default=DEFAULT_ANSWER)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--reward-file", default="/verifier/reward.txt")
    parser.add_argument("--evidence-file", default="/verifier/evidence.json")
    args = parser.parse_args()

    try:
        from scorer import question_scorer
    except Exception as exc:                       # noqa: BLE001 - the environment, not the agent
        return _cannot_measure(args, f"the pinned scorer will not import: "
                                     f"{type(exc).__name__}: {exc}")

    gold_path = Path(args.gold)
    if not gold_path.is_file():
        return _cannot_measure(args, f"no gold at {gold_path}")
    try:
        record = json.loads(gold_path.read_text(encoding="utf-8"))
        gold = str(record["gold"])
    except Exception as exc:                       # noqa: BLE001
        return _cannot_measure(args, f"the gold is not usable: {type(exc).__name__}: {exc}")
    if gold.strip() in ("", "?"):
        return _cannot_measure(
            args, f"gold is the placeholder {gold!r} (the test split's sentinel): this task "
                  f"cannot be scored")

    answer_path = Path(args.answer)
    answer = answer_path.read_text(encoding="utf-8").strip() if answer_path.is_file() else ""
    if not answer:
        return _measure(args, 0.0, "no answer (measured zero): answer.txt is missing or empty",
                        {**_identity(record), "gold": gold})

    transcript = io.StringIO()
    try:
        with contextlib.redirect_stdout(transcript):
            correct = bool(question_scorer(answer, gold))
    except Exception as exc:                       # noqa: BLE001 - a scorer crash is not a wrong answer
        return _cannot_measure(args, f"question_scorer raised {type(exc).__name__}: {exc}")

    return _measure(args, 1.0 if correct else 0.0,
                    "exact match after the official normalization" if correct
                    else "no match after the official normalization",
                    {**_identity(record), "answer": answer, "gold": gold,
                     "scorer_said": transcript.getvalue().strip()[:500]})


if __name__ == "__main__":
    raise SystemExit(main())
