"""Run the task's own reference solution. A control, not a harness.

Why this exists
---------------
When a harness scores 0 on a whole dataset there are two explanations that look
identical from the curve: the harness is weak, or the platform's verify path does not
work. Every task contract this platform has added -- environments, verifiers, containers,
services, container state -- can break in the second way, and a broken verify path is
*quieter* than a broken harness because it produces a plausible zero.

So the platform ships an oracle: the environment hands it the dataset's own reference
solution and it does nothing else. If the oracle does not score, the dataset's check is
not reachable and no harness's score on it means anything yet.

How it finds the solution
-------------------------
The environment puts it at `.oracle/` inside the workdir (`HG_TB_ORACLE=1` in
`data/datasets/terminal_bench.py`). It is a plain directory of scripts plus whatever else the
reference solution ships, so this runs `solve.sh` if there is one and otherwise runs the
first executable it finds.

This file must never contain task knowledge: it is a fixed program that runs whatever it
is handed, which is what keeps it a control rather than a second implementation.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task")
    ap.add_argument("--workdir")
    args = ap.parse_args()
    work = pathlib.Path(args.workdir)
    oracle = work / ".oracle"
    if not oracle.is_dir():
        print(f"oracle: nothing at {oracle}; the dataset supplied no solution",
              file=sys.stderr)
        return 1

    # Terminal-Bench's own convention is that a reference solution lives at
    # `/solution`, and its `solve.sh` refers to that path absolutely -- measured:
    # `cp /solution/headless_terminal.py /app/headless_terminal.py`. So the solution is
    # staged there before it runs. The platform hands it in as a normal SETUP file
    # (relative, inside the workdir) and this control is what puts it where the
    # solution expects to be, so that no dataset has to know about the oracle.
    staged = pathlib.Path("/solution")
    if not staged.exists():
        try:
            subprocess.run(["cp", "-r", str(oracle), str(staged)], check=True,
                           capture_output=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"oracle: could not stage {oracle} at {staged}: {exc}",
                  file=sys.stderr)
            return 1

    script = staged / "solve.sh"
    if not script.is_file():
        candidates = sorted(p for p in staged.rglob("*") if p.is_file())
        if not candidates:
            print(f"oracle: {staged} holds no files", file=sys.stderr)
            return 1
        script = candidates[0]

    proc = subprocess.run(["bash", str(script)], cwd=work,
                          capture_output=True, text=True, errors="replace")
    print(f"oracle: {script.name} exited {proc.returncode}", file=sys.stderr)
    if proc.stderr:
        print(proc.stderr[-2000:], file=sys.stderr)

    (work / "trace.jsonl").write_text(json.dumps({
        "harness_identity": {"agent_model": "oracle", "harness": "oracle 1"},
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
