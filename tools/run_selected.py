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
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_tests.py"
sys.path.insert(0, str(ROOT))
from tools.ci_select import load_plan  # noqa: E402
from tools.run_tests import QualityReceipt, receipt_argv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--full-if-empty", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print the command, run nothing")
    parser.add_argument("--output-json", default="", help="atomic quality receipt (no environment secrets)")
    args = parser.parse_args()
    discovered = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py"))
    receipt = QualityReceipt(args.output_json, discovered, [])
    receipt.data["selector_argv"] = receipt.data["argv"]

    try:
        plan = load_plan(args.plan)
    except (OSError, ValueError) as exc:
        receipt.finish(2, "invalid")
        parser.error(f"invalid plan: {exc}")
    suite = plan["suite"]
    state = plan["state"]
    if args.full or (args.full_if_empty and not suite):
        state = "full"
    planned = discovered if state in ("full", "deferred") else [f"tests/quality/{name}" for name in suite]
    receipt.data.update(state=state, planned=planned, unexecuted=list(planned),
                        skipped=[f for f in discovered if f not in planned] if state != "deferred" else [],
                        deferred=discovered if state == "deferred" else [])
    receipt.save()
    if args.jobs < 1:
        return receipt.finish(2, "invalid")
    if state == "deferred":
        print("state=deferred: this change selects everything; the full suite is deferred "
              "to the nightly run or a manual dispatch")
        return receipt.finish(0, "deferred")
    if state == "selected" and not suite:
        print(f"state=selected: nothing in the suite can reach this change "
              f"(base={plan.get('base') or '<none>'}); the guards ran in the checks job")
        return receipt.finish(0, "empty")

    command = [sys.executable, str(RUNNER), "--jobs", str(args.jobs)]
    if state == "selected":
        # --fast is a discovery convenience, not permission to prune an explicit plan.
        for name in suite:
            command += ["--file", f"tests/quality/{name}"]
    if args.output_json:
        command += ["--output-json", str(receipt.path)]
    receipt.data["runner_argv"] = receipt_argv(command)
    print(f"state={state}; running:", " ".join(command), flush=True)
    if args.dry_run:
        return receipt.finish(0, "dry_run")
    receipt.data["status"] = "running"
    receipt.save()
    try:
        if not receipt.path:
            return subprocess.run(command, cwd=ROOT).returncode
        env = {**os.environ, "HG_QUALITY_SELECTOR_ARGV_JSON":
               json.dumps(receipt.data["selector_argv"])}
        child_process = subprocess.Popen(command, cwd=ROOT, env=env)
        try:
            code = child_process.wait()
        except KeyboardInterrupt:
            # Never rewrite the shared receipt until the child writer is reaped.
            child_process.terminate()
            try:
                child_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child_process.kill()
                child_process.wait()
            code = 130
    except OSError:
        code = 2
    if receipt.path:
        # The child checkpoints this same receipt throughout execution. Keep that
        # evidence, including on interruption, then restore selector provenance.
        child = json.loads(receipt.path.read_text(encoding="utf-8"))
        child.update(state=state, selector_argv=receipt.data["selector_argv"],
                     runner_argv=receipt_argv(command))
        receipt.data = child
    if code < 0 or code == 130:
        status = "interrupted"
    elif receipt.data["status"] in ("completed", "invalid", "empty"):
        status = receipt.data["status"]
    else:
        status = "error"
    return receipt.finish(code, status)


if __name__ == "__main__":
    sys.exit(main())
