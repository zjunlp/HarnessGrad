#!/usr/bin/env python3
"""A CONTROL, NOT A HARNESS. Copies the reference workbook into place and names it.

SpreadsheetBench's reference solution is a **file**: the golden workbook the official evaluator
compares against. `base_harness/oracle` is the platform's oracle for a dataset whose solution is
a program it can run, and staging a file at `/solution` with `cp -r` is a container operation a
host run without root cannot perform -- the same reason ALFWorld's and ScienceWorld's controls
exist. This one is deliberately dumber than a harness: read `.oracle/result.xlsx`, copy it to the
contract's filename, write that filename into `answer.txt`, stop.

Its grade cannot come from anything it says -- the check reads the workbook it left behind and
compares it with its own trusted copy of the golden file.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task")
    parser.add_argument("--workdir")
    args = parser.parse_args()
    work = pathlib.Path(args.workdir).resolve()

    oracle = work / ".oracle" / "result.xlsx"
    if not oracle.is_file():
        print(f"spreadsheetbench-oracle: no reference workbook at {oracle}", file=sys.stderr)
        return 0
    shutil.copy2(oracle, work / "result.xlsx")
    proc = subprocess.run([sys.executable, str(work / "qa" / "bin" / "qa"), "submit",
                           "result.xlsx"], cwd=work, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        print(f"spreadsheetbench-oracle: the CLI exited {proc.returncode}: "
              f"{proc.stderr[-300:]}", file=sys.stderr)
        return proc.returncode
    (work / "trace.jsonl").write_text(json.dumps({
        "harness_identity": {"agent_model": "oracle",
                             "harness": "spreadsheetbench-oracle 1"},
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }) + "\n")
    print("spreadsheetbench-oracle: copied the reference workbook into place")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
