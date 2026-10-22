#!/usr/bin/env python3
"""A stand-in agent CLI, so the wrapper can be tested without installing one.

Why this exists
---------------
`base_harness/cli_agent/` is a wrapper around agent CLIs, and testing it against the
real thing means installing a CLI, configuring credentials for it, and paying for its
model calls. None of that tests the *wrapper*: what has to be exercised is the
translation -- prompt in, work in the working directory, session state under `$HOME`,
token counts out.

So this behaves like the class of program the wrapper targets, using none of them:

    fake_cli "do the thing"        work in the CWD, print tokens, exit 0
    fake_cli --version             what a CLI prints when the wrapper asks
    echo prompt | fake_cli -       read the prompt from stdin

It writes `$HOME/.fakecli/sessions/<n>.json` like every real one does, which is what
makes it a genuine test of the platform's per-task HOME: on a sandbox that does not
provide one, this dies exactly where Codex would.

The work it does is keyword-driven against `data/verify_demo.py`, so the demo run
produces real verdicts rather than a wall of zeros. It is not a harness and it is not
a model -- it is a fixed script wearing a CLI's clothes.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

VERSION = "fake-cli 0.3.1"


def _work(goal: str, cwd: pathlib.Path) -> str:
    """Do something plausible for the task, and say what."""
    did = []
    text = goal.lower()

    if "broken.py" in text:
        (cwd / "broken.py").write_text("def add(a, b):\n    return a + b\n")
        did.append("rewrote broken.py")

    if "report.txt" in text:
        (cwd / "report.txt").write_text("harness ok\n")
        did.append("wrote report.txt")

    if "seed.py" in text:
        # Running it is the whole task; a CLI would do this through its shell tool.
        (cwd / "answer.txt").write_text("42\n")
        did.append("ran seed.py and answered 42")

    if "out/" in text or "out`" in text:
        out = cwd / "out"
        out.mkdir(exist_ok=True)
        (out / "a.txt").write_text("alpha\n")
        (out / "b.txt").write_text("beta\n")
        did.append("created out/a.txt and out/b.txt")

    if not did:
        (cwd / "answer.txt").write_text("done\n")
        did.append("wrote a placeholder answer")

    return ", ".join(did)


def main() -> int:
    if "--version" in sys.argv:
        print(VERSION)
        return 0

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("-p", "--print", dest="prompt", nargs="?", default=None)
    ap.add_argument("prompt_positional", nargs="?", default=None)
    ap.add_argument("-", dest="stdin_marker", action="store_true")
    args, _ = ap.parse_known_args()

    prompt = args.prompt or args.prompt_positional
    if args.stdin_marker or (prompt is None and not sys.stdin.isatty()):
        prompt = sys.stdin.read()
    prompt = prompt or ""

    # Every one of these CLIs keeps session state under $HOME. This is the line that
    # fails first on a sandbox that does not provide a writable one.
    home = pathlib.Path(os.environ.get("HOME") or pathlib.Path.home())
    sessions = home / ".fakecli" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    n = len(list(sessions.glob("*.json"))) + 1
    (sessions / f"{n}.json").write_text(json.dumps({"prompt": prompt[:200]}))

    cwd = pathlib.Path.cwd()
    summary = _work(prompt, cwd)

    # A token report in the CLI's own format. The wrapper has to translate this; if it
    # does not, the platform reads zero, and RRSI's cost rule is fed a constant.
    print(f"fake-cli: {summary}")
    print("input tokens: 1870")
    print("output tokens: 412")
    print("calls: 7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
