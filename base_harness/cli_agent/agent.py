#!/usr/bin/env python3
"""A wrapper: any prompt-taking agent CLI, as a HarnessGrad harness.

Why this file exists
--------------------
The harnesses worth measuring are increasingly CLIs -- Claude Code, Codex, OpenCode
and their relatives -- and none of them can be a HarnessGrad harness directly. They
do not take `--task`/`--workdir`, they do not write `answer.txt`, they keep their
session state under `$HOME`, and they read their model from their own config file
rather than from `HG_AGENT_*`.

So this is the thin translation layer, and it is deliberately the *only* place that
knows anything about a CLI:

    our contract            this wrapper                  the CLI
    --task <json>     ->    render the goal     ->   the prompt
    --workdir         ->    cwd                 ->   where it works
    (nothing)         ->    HG_CLI_ENV mapping  ->   the credentials it expects
    trace + answer    <-    parse its output    <-   what it printed

**Editing this file is how you support a new CLI.** The knowledge lives in
configuration (`HG_CLI`, `HG_CLI_ENV`, `HG_CLI_MODEL`), not in branches here, because
the next CLI is always slightly different and branches for each one do not compose.

What it deliberately does not do
--------------------------------
* **No model knowledge.** It never assumes which variable a CLI reads; `HG_CLI_ENV`
  declares the mapping. That is also how the platform's `HG_AGENT_*` values reach a
  CLI without the platform having to know the CLI.
* **No scoring.** It cannot see the verifier, and it does not report a score. It
  declares what it *was* (`harness_identity` in the trace) and exits.
* **No sandboxing of its own.** The platform's sandbox is the boundary. Whether the
  CLI's own approval prompts are bypassed is configuration, and it is recorded
  because "the harness's own sandbox is off" is a security-relevant fact.

Configuration (all optional except `HG_CLI`)
--------------------------------------------
    HG_CLI            the command, shell-quoted: e.g. `claude -p`
    HG_CLI_PROMPT     how the prompt reaches it: `argv` (default) | `stdin`
    HG_CLI_ENV        JSON: extra env for the CLI, `$VAR` expanded from ours
    HG_CLI_MODEL      what to declare as the model (falls back to HG_AGENT_MODEL)
    HG_CLI_NAME       what to declare as the harness (default: argv[0]'s basename)
    HG_CLI_TIMEOUT_S  its own timeout, so a hung CLI is reported rather than killed
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_PROMPT = """Complete this task in the current working directory.

{goal}

When you are finished: if the task asks for a single answer, write exactly that
answer (and nothing else) to `answer.txt` in the current working directory.
Otherwise leave your work in place.
"""


def _cli_env() -> tuple[dict, list[str]]:
    """Expand `HG_CLI_ENV`. Returns (extra env, names that were requested but unset).

    `$VAR` is expanded from this harness's own environment, which is how the
    platform's `HG_AGENT_API_KEY` reaches a CLI that calls it `ANTHROPIC_API_KEY`
    without the platform knowing either name. A name that is asked for and missing is
    reported rather than silently dropped: a CLI that starts without credentials
    fails with its own error message, which reads as "this harness is broken".
    """
    raw = os.environ.get("HG_CLI_ENV", "").strip()
    if not raw:
        return {}, []
    try:
        declared = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"HG_CLI_ENV is not JSON: {exc}")
    out, missing = {}, []
    for name, value in declared.items():
        text = str(value)
        if text.startswith("$"):
            source = text[1:]
            if source not in os.environ:
                missing.append(source)
                continue
            text = os.environ[source]
        out[name] = text
    return out, missing


def _write_trace(workdir: Path, events: list[dict]) -> None:
    """One JSON object per line, the last one carrying `usage`.

    `usage` is required by the contributor spec and read by the platform
    (`eval/tracing.py:_last_usage`). A wrapper that does not translate its CLI's token
    report leaves the platform reading zero, which does not disable a cost rule -- it
    feeds it a constant zero, and RRSI's rule then admits every candidate.
    """
    path = workdir / "trace.jsonl"
    with path.open("a") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    task = json.loads(Path(args.task).read_text())

    raw = os.environ.get("HG_CLI", "").strip()
    if not raw:
        _write_trace(workdir, [{"error": "HG_CLI is unset: no CLI to run"}])
        print("wrapper: HG_CLI is not set, so there is no CLI to run", file=sys.stderr)
        return 2

    argv = shlex.split(raw)
    prompt = os.environ.get("HG_CLI_PROMPT_TEMPLATE") or DEFAULT_PROMPT
    prompt = prompt.replace("{goal}", task.get("goal", ""))
    use_stdin = (os.environ.get("HG_CLI_PROMPT", "argv") == "stdin")
    if not use_stdin:
        argv = [*argv, prompt]

    try:
        extra_env, missing = _cli_env()
    except SystemExit as exc:
        _write_trace(workdir, [{"error": str(exc)}])
        print(f"wrapper: {exc}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env.update(extra_env)
    # Keep our own variables out of the CLI's environment: it does not know them, and
    # an agent that can read `HG_CLI_ENV` can read the credentials it expands.
    for name in ("HG_CLI_ENV", "HG_CLI", "HG_CLI_PROMPT_TEMPLATE"):
        env.pop(name, None)

    timeout = float(os.environ.get("HG_CLI_TIMEOUT_S", "0") or 0) or None

    _write_trace(workdir, [{
        "step": 0,
        "cli": argv[0] if argv else None,
        "cwd": str(workdir),
        "prompt_via": "stdin" if use_stdin else "argv",
        "credentials_missing": missing,
    }])

    try:
        proc = subprocess.run(
            argv, cwd=workdir, env=env, timeout=timeout,
            input=prompt if use_stdin else None,
            capture_output=True, text=True, errors="replace")
    except FileNotFoundError as exc:
        _write_trace(workdir, [{"error": f"the CLI does not exist: {exc}"}])
        print(f"wrapper: the CLI does not exist: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        _write_trace(workdir, [{"error": f"the CLI exceeded {timeout}s"}])
        print(f"wrapper: the CLI exceeded {timeout}s", file=sys.stderr)
        return 1

    # The CLI's own output, kept whole on stderr so the platform's stdout stays clean
    # and a reader can still see what the agent said.
    if proc.stderr:
        sys.stderr.write(proc.stderr[-4000:])

    name = os.environ.get("HG_CLI_NAME") or Path(argv[0]).name
    identity = {
        "harness": name,
        # The CLI reads its model from wherever it likes; `HG_CLI_MODEL` is the
        # operator saying what that was, and `HG_AGENT_MODEL` is the platform's
        # convention as a fallback. See INTERFACE.md §3.
        "agent_model": os.environ.get("HG_CLI_MODEL")
                       or os.environ.get("HG_AGENT_MODEL") or None,
        "agent_backend": "cli",
    }
    _write_trace(workdir, [
        {"harness_identity": identity},
        # Translate what we can. A CLI that reports nothing gets no token count rather
        # than a zero: a zero that means "not reported" is indistinguishable from a
        # real zero, and a cost rule fed one is not a weak rule but no rule.
        {"usage": _usage_from(proc.stdout, proc.stderr)},
    ])

    print(f"wrapper: {name} exited {proc.returncode}")
    # The CLI's exit code is its own. The task's outcome is the platform's verdict on
    # what was left behind -- a CLI that exits non-zero after doing the work still
    # completed the task, and a CLI that exits 0 without doing it still did not.
    return 0


def _usage_from(stdout: str, stderr: str) -> dict:
    """Whatever token counts the CLI printed, if it printed any.

    Deliberately conservative: it looks for a line the CLI itself labels, and returns
    an empty block when it finds none. Guessing a count would be worse than reporting
    nothing, because the platform cannot tell a guess from a measurement.
    """
    import re

    text = f"{stdout}\n{stderr}"
    counts: dict = {}
    for label, key in (("input", "input_tokens"), ("output", "output_tokens"),
                       ("total", None), ("calls", "calls")):
        match = re.search(rf"{label}\s*tokens?\s*[:=]\s*(\d+)", text, re.I)
        if match and key:
            counts[key] = int(match.group(1))
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
