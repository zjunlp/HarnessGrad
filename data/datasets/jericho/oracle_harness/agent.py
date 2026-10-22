#!/usr/bin/env python3
"""A CONTROL, NOT A HARNESS. Replays a scripted Jericho command list through `jer`.

`base_harness/oracle` is the platform's own oracle and is the right tool for a dataset whose
reference solution is a program. Jericho's reference solution is a **list of game commands**
(`FrotzEnv.get_walkthrough()`, valid at the game's official seed), and the platform's oracle
stages its solution at `/solution` with `cp -r`, which a host run without root cannot create.
So this control exists for the same reason ALFWorld's and ScienceWorld's do, and it is kept
deliberately dumber than a harness: it reads `.oracle/actions.json`, runs `jer do <command>`
once per command, calls `jer done`, and reports nothing it observed.

Its grade cannot come from anything it says -- the check replays the CLI's log in a fresh game
and reads the game's own score.

It is reached only through `data/datasets/jericho/control.py --platform`. It is never offered as a
harness a method can evolve: it contains no reasoning and would teach a method nothing.
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
        proc = subprocess.run([sys.executable, str(work / "jer"), "do", action],
                              cwd=work, capture_output=True, text=True, errors="replace")
        if proc.returncode != 0:
            print(f"oracle harness: `jer do {action!r}` exited {proc.returncode}: "
                  f"{proc.stderr[-400:]}", file=sys.stderr)
            break
        ran += 1
    subprocess.run([sys.executable, str(work / "jer"), "done"], cwd=work,
                   capture_output=True, text=True, errors="replace")

    (work / "trace.jsonl").write_text(json.dumps({
        "harness_identity": {"agent_model": "oracle", "harness": "jericho-oracle 1"},
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }) + "\n")
    print(f"oracle harness: replayed {ran} of {len(actions)} scripted command(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
