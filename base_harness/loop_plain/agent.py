#!/usr/bin/env python3
"""A control base harness: a minimal ReAct loop that never answers.

Deliberately the simplest thing that satisfies the contract in
`docs/writing_a_harness.md`. Its job is not to be strong -- it is to be:

  1. a valid starting point that a method can improve, and
  2. the proof that the contract is implementable at all.

A method may replace every file here. That is the point.

**What is deliberately absent, and why.** This harness has no context policy, no
output bounding and no memory. Those are *mechanisms*: named, switchable
behaviours that a method adds in order to improve the harness. Shipping a set of
them by default would mean shipping one particular experiment's design as if it
were the platform's, and would make "this harness is thin" a claim nobody can
check. A base harness with nothing in it is the honest starting point; what a
method adds on top is the measurement.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


#: How many model calls one task gets. **A floor on capability, not a tuning knob.**
#:
#: Measured at 8, on Terminal-Bench, 4 rounds x 5 tasks: four of the five tasks used all
#: eight and produced no graded artifact at all -- `build-pmars` was still installing
#: packages, `bn-fit-modify` had just finished installing pandas/numpy/sklearn and
#: printed one correlation matrix, `chess-best-move` had opened the PNG and never wrote
#: `/app/move.txt`. The score was 0.0 on every task in every round, which is not a weak
#: harness being measured: it is a floor effect, and a floor makes every method look
#: identical because there is no gradient for one to move.
#:
#: Raising this does not make the harness cleverer -- `legacy/adapters/harnessgrad_domain/`
#: already names this lever as the trap ("helps only if the model is already using the
#: steps it has"). Here the trace shows it *was* using them, which is exactly when the
#: lever is legitimate. What it costs is bounded by the task, not by this number: the
#: wall clock is capped by the environment's `agent_timeout_s` (900 s for most
#: Terminal-Bench tasks, 3600 s for `bn-fit-modify`). A step is fast (~0.2 s model round
#: trip through the local gateway), so the extra ceiling mostly buys back the steps that
#: environment setup eats. The loop still stops the moment the model answers -- the cap
#: is a ceiling, not a quota.
MAX_STEPS = int(os.environ.get("HG_AGENT_MAX_STEPS", "32"))

SYSTEM = """You are an agent solving a task in a working directory.

You have one tool: run a shell command.

Reply with EXACTLY one of:
  {"tool": "bash", "command": "<shell command>"}
  {"answer": "<your final answer>"}

Rules:
- One action per reply. No prose outside the JSON.
- To inspect files use shell commands (ls, cat, grep, ...).
- Give the final answer only when you are confident.
"""


# ---------------------------------------------------------------- backends ---

def _backend() -> str:
    """Which completion backend to use.

    `mock` exists so the framework can be exercised end-to-end with zero network
    and zero cost. Any real curve must declare its backend and model on the curve
    point (INTERFACE.md §3, `identity.agent_model`).
    """
    return os.environ.get("HG_AGENT_BACKEND", "mock")


def _call_mock(messages: list[dict]) -> str:
    """Deterministic stand-in: read answer.txt if present, else list the dir."""
    # This variant never answers: it only ever looks around. It is the control
    # harness -- the floor -- and it exists so that a method's improvement has
    # something to be measured against that is not the absence of a run.
    return json.dumps({"tool": "bash", "command": "ls -a"})


# --------------------------------------------------------------- usage ---

#: Token usage accumulated over the run, filled by whichever backend is active.
#: Read into the trace at the end. Recorded because a mechanism that spends model
#: calls (history_summary does) is only comparable against a free one if the
#: spend is visible -- and because a harness whose cost is unknown cannot be
#: ranked against a cheaper one that scores the same.
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "retries": 0}


def _record_usage(usage) -> None:
    """Fold provider token counts into the run's total.

    The call count is NOT incremented here: it is incremented by `_complete`,
    which is the one place every model call goes through. Counting calls at the
    provider boundary would miss the mock backend entirely -- and a mechanism that
    spends model calls on a mock run would then report zero cost, which is exactly
    the invisible-spend the usage block exists to prevent.
    """
    if usage is None:
        return
    # Provider fields that are absent stay absent rather than becoming zero: a
    # zero that means "not reported" is indistinguishable from a real zero.
    for attr, key in (("prompt_tokens", "input_tokens"),
                      ("completion_tokens", "output_tokens")):
        v = getattr(usage, attr, None)
        if isinstance(v, int):
            USAGE[key] += v


def _call_openai(messages: list[dict]) -> str:
    """One completion, with a deadline and bounded retries.

    A harness that dies on a transient provider error loses a whole task run, and
    an unbounded retry loop turns a provider outage into a hung experiment. Both
    failures are silent in a batch unless the harness is defensive here.
    """
    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ["HG_AGENT_BASE_URL"],
        api_key=os.environ["HG_AGENT_API_KEY"],
        timeout=float(os.environ.get("HG_AGENT_TIMEOUT_S", "180")),
        max_retries=0,          # retried here, so the count is ours to record
    )
    model = os.environ["HG_AGENT_MODEL"]
    attempts = int(os.environ.get("HG_AGENT_RETRIES", "3"))
    last: Exception | None = None

    for attempt in range(attempts):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.0,
            )
            _record_usage(getattr(resp, "usage", None))
            return resp.choices[0].message.content or ""
        except Exception as exc:                      # noqa: BLE001
            last = exc
            if attempt + 1 < attempts:
                USAGE["retries"] += 1
                time.sleep(min(2 ** attempt, 30))
    # Out of attempts: raise, so the caller records a harness failure rather than
    # scoring an empty answer as a wrong one.
    raise RuntimeError(f"{attempts} attempts failed, last: {last}") from last


def _complete(messages: list[dict]) -> str:
    """Every model call in this harness goes through here, so this is where the
    count is kept. Counting anywhere else means some caller is invisible."""
    USAGE["calls"] += 1
    return _call_mock(messages) if _backend() == "mock" else _call_openai(messages)


# ------------------------------------------------------------- the loop -----

def _parse(reply: str) -> dict:
    """The **first JSON object** in a reply, ignoring anything after it.

    Tolerant extraction is not a nicety here; this function decides whether a task runs
    at all, and one of its failure modes cost two whole runs.

    The first version tried `json.loads(reply)`, then fell back to slicing from the first
    `{` to the **last** `}`. Both fail on the same reply shape, and that shape is what the
    measured model actually emits: asked for one action, it sometimes answers with two or
    three JSON objects, one per line --

        {"tool": "bash", "command": "cat /app/filter.py"}}
        {"answer": ""}

    (note the stray brace as well). `json.loads` reports `Extra data: line 1 column 50`,
    and the `rfind` fallback slices `{...}}{...}`, which is not JSON either. The harness
    treats an unparseable reply as a harness failure and **breaks the loop**, so the task
    ends at step 0 with an empty answer and scores 0.

    Measured on `loop-terminal_bench-41133`, round 0: four of five tasks died this way
    after a single model call (`calls: 1`, `parse_error: Extra data`), and every round
    after it was the same because the round starts from a harness that cannot do anything.
    The old run `loop-terminal_bench-26452c` produced single-object replies and got 8
    calls per task -- same code, same prompt -- so this is a property of the model's
    output that the parser has to absorb, not something the parser caused.

    `raw_decode` from the first `{` is the whole fix: it returns the first complete
    object and says where it stopped, so trailing objects, stray braces and prose around
    the JSON all stop mattering. `base_harness/loop_rrsi_parse/` already shipped exactly
    this ; it is the same code, because a parser that differs between two base harnesses
    is a difference between two candidates that the platform made rather than the method.
    """
    reply = reply.strip()
    if reply.startswith("```"):
        reply = reply.split("```")[1]
        reply = reply[4:] if reply.startswith("json") else reply
    decoder = json.JSONDecoder()
    for i, ch in enumerate(reply):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(reply[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found in reply")


def run(task: dict, workdir: Path) -> str:
    """Execute one task. Returns the answer string.

    The trace written to <workdir>/trace.jsonl is what the framework collects
    and hands to the Trainer (INTERFACE.md §4.1).
    """
    trace_path = workdir / "trace.jsonl"
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(task)},
    ]

    answer = ""
    with trace_path.open("w") as trace:
        for step in range(MAX_STEPS):
            reply = _complete(messages)
            trace.write(json.dumps({"step": step, "reply": reply}) + "\n")
            messages.append({"role": "assistant", "content": reply})

            try:
                action = _parse(reply)
            except Exception as exc:  # malformed action is a harness failure
                trace.write(json.dumps({"step": step, "parse_error": str(exc)}) + "\n")
                break

            if "answer" in action:
                answer = str(action["answer"])
                break

            cmd = action.get("command", "")
            proc = subprocess.run(
                cmd, shell=True, cwd=workdir, capture_output=True, text=True,
                errors="replace"  # arbitrary command bytes must not crash the harness
            )
            out = proc.stdout + proc.stderr
            trace.write(json.dumps({"step": step, "command": cmd,
                                    "exit": proc.returncode, "output": out}) + "\n")
            messages.append({"role": "user", "content": f"exit={proc.returncode}\n{out}"})

        # What the run cost. Kept because a harness that cannot say what it spent
        # cannot be compared with a cheaper one at the same score -- and because
        # `USAGE` is the only place the model calls are counted.
        trace.write(json.dumps({"usage": dict(USAGE)}) + "\n")

    (workdir / "answer.txt").write_text(answer)
    return answer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    task = json.loads(Path(args.task).read_text())
    run(task, Path(args.workdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
