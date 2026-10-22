#!/usr/bin/env python3
"""A CONTROL, NOT A HARNESS. Replays a scripted action list through the `alf` CLI.

`base_harness/oracle` is the platform's own oracle, and it is the right tool for a
dataset whose reference solution is a program it can run. ALFWorld's reference solution
is a *list of text commands* (the `walkthrough` array inside the game file), and the
platform's oracle stages its solution at `/solution` with `cp -r`, which a host run
without root cannot create. So this control exists for the same reason and is kept
deliberately dumber than a harness: it reads `.oracle/actions.json`, runs
`alf do <action>` once per action, calls `alf done`, and reports nothing it observed.
Its grade cannot come from anything it says -- the check replays the CLI's log in a
fresh environment.

It is reached only through `data/datasets/alfworld/control.py --platform`. It is never offered
as a harness a method can evolve: it contains no reasoning and would teach a method
nothing.
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

    oracle = work / ".oracle" / "actions.json"
    actions: list[str] = []
    if oracle.is_file():
        actions = [str(a) for a in json.loads(oracle.read_text())]

    ran = 0
    for action in actions:
        proc = subprocess.run([sys.executable, str(work / "alf"), "do", action],
                              cwd=work, capture_output=True, text=True, errors="replace")
        if proc.returncode != 0:
            print(f"oracle harness: `alf do {action!r}` exited {proc.returncode}: "
                  f"{proc.stderr[-400:]}", file=sys.stderr)
            break
        ran += 1
    subprocess.run([sys.executable, str(work / "alf"), "done"], cwd=work,
                   capture_output=True, text=True, errors="replace")

    (work / "trace.jsonl").write_text(json.dumps({
        "harness_identity": {"agent_model": "oracle", "harness": "alfworld-oracle 1"},
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }) + "\n")
    print(f"oracle harness: replayed {ran} of {len(actions)} scripted action(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
