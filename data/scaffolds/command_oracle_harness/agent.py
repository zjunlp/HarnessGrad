#!/usr/bin/env python3
"""A CONTROL, NOT A HARNESS. It copies the reference artefact into place and stops.

Every `command` dataset's reference solution is a **file** (or a small set of them): a golden
workbook, a correct program, a label. The dataset names those files by declaring
`oracle_files(task)`, `data/scaffolds/command_control.py` stages them at `.oracle/`, and this control
copies them into the workdir exactly as a correct agent would have left them.

It is deliberately dumber than a harness. It reads nothing from the task, decides nothing, and
its grade cannot come from anything it says -- the check reads the artefact it left behind and
compares it with its own trusted copy. It exists so that `--platform` can prove a dataset's
VERIFY path reaches reward 1 through `eval.run.runner.run_one`, which is a different claim from
"the check works when I run it by hand".

Generated datasets get this harness by copy (`tools/new_dataset.py`); it lives here so that a
change to it reaches every `command` dataset at once rather than the newest one.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task")
    parser.add_argument("--workdir")
    args = parser.parse_args()
    work = pathlib.Path(args.workdir).resolve()

    staging = work / ".oracle"
    if not staging.is_dir():
        print(f"command-oracle: no reference artefact at {staging}", file=sys.stderr)
        return 0
    copied = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        target = work / path.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(str(path.relative_to(staging)))
    (work / "trace.jsonl").write_text(json.dumps({
        "harness_identity": {"agent_model": "oracle",
                             "harness": "command-oracle 1"},
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }) + "\n")
    print(f"command-oracle: copied {len(copied)} reference file(s): {', '.join(copied)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
