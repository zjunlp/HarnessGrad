#!/usr/bin/env python3
"""Run the tests a change can reach, from a plan `tools/ci_select.py` wrote.

Why this exists
---------------
The first version asked a shell step to hold the selection as a space-joined string, subtract
the guard names with a `grep`, and rebuild `--file` arguments in a `for` loop. Every one of
those steps is a place where an empty or partially-quoted value turns into "no `--file`" --
which is not an error, it is the whole suite, silently. Doing it here means the plan is a file,
the subtraction is a set difference, and the command is printed before it runs.

Usage
-----
    python3 tools/ci_select.py --base <ref> --head <ref> --plan plan.json
    python3 tools/run_selected.py --plan plan.json [--jobs 4] [--full-if-empty]

`--full-if-empty` is for the nightly job: a plan that selects nothing there means "measure the
whole tree", which is the point of running it on a schedule.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_tests.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--full-if-empty", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print the command, run nothing")
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    suite = [] if args.full or plan.get("full") else list(plan.get("suite") or [])
    if not suite and not args.full_if_empty:
        print(f"nothing in the suite can reach this change "
              f"(base={plan.get('base') or '<none>'}); the guards ran in the checks job")
        return 0
    command = [sys.executable, str(RUNNER), "--fast", "--jobs", str(args.jobs)]
    for name in suite:
        command += ["--file", name]
    print("running:", " ".join(command), flush=True)
    if args.dry_run:
        return 0
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
