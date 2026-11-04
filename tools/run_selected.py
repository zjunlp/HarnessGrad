#!/usr/bin/env python3
"""Execute a validated CI plan in one of three explicit states.

selected: run exactly the named quality files, including slow files; empty runs nothing.
deferred: a conservative full fallback, left for schedule/manual execution.
full: run the entire suite, without --fast or --file filters.

--full explicitly requests full execution regardless of the valid plan's selection.
--full-if-empty is the legacy nightly option: promote any empty selection to full.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_tests.py"
sys.path.insert(0, str(ROOT))
from tools.ci_select import load_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--full-if-empty", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print the command, run nothing")
    args = parser.parse_args()

    try:
        plan = load_plan(args.plan)
    except (OSError, ValueError) as exc:
        parser.error(f"invalid plan: {exc}")
    suite = plan["suite"]
    state = plan["state"]
    if args.full or (args.full_if_empty and not suite):
        state = "full"
    if state == "deferred":
        print("state=deferred: this change selects everything; the full suite is deferred "
              "to the nightly run or a manual dispatch")
        return 0
    if state == "selected" and not suite:
        print(f"state=selected: nothing in the suite can reach this change "
              f"(base={plan.get('base') or '<none>'}); the guards ran in the checks job")
        return 0

    command = [sys.executable, str(RUNNER), "--jobs", str(args.jobs)]
    if state == "selected":
        # --fast is a discovery convenience, not permission to prune an explicit plan.
        for name in suite:
            command += ["--file", f"tests/quality/{name}"]
    print(f"state={state}; running:", " ".join(command), flush=True)
    if args.dry_run:
        return 0
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
