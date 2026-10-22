#!/usr/bin/env python3
"""A CONTROL, NOT A HARNESS. Reads `.oracle/answer.txt` and submits it through `qa`.

Every plain-text QA dataset in this repository scores the same artifact -- the bytes of
`answer.txt` -- so every one of them can share one control harness. That is the whole
of this program: read what the dataset staged, hand it to the CLI, stop.

Why it is deliberately this dumb: the platform's own oracle runs a dataset's reference
*solution*, and for these datasets the reference solution is one string. A harness that
computed that string itself would be a second implementation of the dataset's gold, and
a control that agrees with the check because both are wrong is the failure mode the
control exists to detect. The gold is staged by the dataset (from the same field the
check loads), so a disagreement shows up as a failed control.

It is reached only through `data/scaffolds/qa/<name>/control.py --platform`. It is never offered
as a harness a method can evolve.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task")
    parser.add_argument("--workdir")
    args = parser.parse_args()
    work = pathlib.Path(args.workdir).resolve()

    # Three channels, in the order the control stages them: the adapter's **own**
    # submission file (`ORACLE_SUBMISSION` -- 2WikiMultihopQA's evidence triples, which the
    # pair schema cannot express), the shared structured prediction, then plain text.
    submission = work / ".oracle" / "submission.json"
    structured_oracle = work / ".oracle" / "prediction.json"
    oracle = work / ".oracle" / "answer.txt"
    structured = structured_oracle.is_file()
    if submission.is_file():
        argument = "@.oracle/submission.json"
        verb = "submit"
    elif structured:
        argument = "@.oracle/prediction.json"
        verb = "submit-json"
    else:
        argument = None
        verb = "submit"
    if argument is None and not oracle.is_file():
        print(f"qa-oracle: no reference answer under {work / '.oracle'}; this control only "
              f"runs when the dataset staged one", file=sys.stderr)
        return 0

    # The CLI's location is the dataset's `SETUP` decision, so it is looked up rather
    # than assumed: this harness runs for every QA dataset and must not encode one
    # layout. `qa/bin/qa` is `data/scaffolds/qa/base.py::CLI_REL`, and the search here is what
    # keeps that a fact about the current tree instead of a second declaration.
    candidates = [work / "qa" / "bin" / "qa", work / "qa"]
    cli = next((c for c in candidates if c.is_file()), None)
    if cli is None:
        print(f"qa-oracle: no `qa` CLI under {work}", file=sys.stderr)
        return 1
    proc = subprocess.run([sys.executable, str(cli), verb,
                           argument if argument is not None
                           else oracle.read_text(encoding="utf-8")],
                          cwd=work, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        print(f"qa-oracle: `qa submit` exited {proc.returncode}: "
              f"{proc.stderr[-400:]}", file=sys.stderr)
        return proc.returncode

    # The trace is how the platform records which model answered and what it cost. This
    # control spent nothing and made no model call, and says so rather than omitting the
    # file: an auditor reading the record should see a control, not an empty trace.
    (work / "trace.jsonl").write_text(json.dumps({
        "harness_identity": {"agent_model": "oracle", "harness": "qa-oracle 1"},
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }) + "\n")
    print("qa-oracle: submitted the dataset's reference prediction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
