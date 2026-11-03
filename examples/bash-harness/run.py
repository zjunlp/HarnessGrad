#!/usr/bin/env python3
"""Entry point the platform invokes; the harness itself is `run.sh`.

**Why this file exists, and it is not decoration.** The platform starts every
entrypoint the same way -- `python <entrypoint> --task … --workdir …`
(`eval/run/runner.py`: `argv = [python, str(repo / entry), …]`) -- so a harness
whose entrypoint is a shell script is handed to the Python interpreter and dies
before its first line:

    File ".../run.sh", line 8
      *) echo "unknown argument: $1" >&2; exit 2 ;;
       ^
    SyntaxError: unmatched ')'

That is measured on this repository, not inferred: it is what
`python3 tools/validate_harness.py examples/bash-harness --smoke` printed while
the manifest named `run.sh`, and what a demo run recorded in `harness_failed`
for all four tasks. `docs/writing_a_harness.md` §4 said a shell script was
acceptable; the invocation above is what the code actually does.

So the shell harness wears a Python collar: this file parses nothing, decides
nothing and writes nothing -- it re-executes `run.sh` in bash, forwards the
arguments verbatim and returns bash's exit code. `run.sh` remains the whole
harness, and `bash run.sh --task … --workdir …` still works by hand.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.call([bash, str(Path(__file__).resolve().parent / "run.sh"), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
