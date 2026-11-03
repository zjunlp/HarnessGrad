#!/usr/bin/env python3
"""Check a harness trace against the adapter contract (`docs/adapter-spec.md`).

    python3 tools/check_trace_contract.py runs/<run-id>            # every trace it holds
    python3 tools/check_trace_contract.py path/to/trace.jsonl
    python3 tools/check_trace_contract.py path/to/trace.jsonl --json

Why a checker and not a paragraph
---------------------------------
The contract that matters is the one the **method** reads, and the method reads
`eval/observe/tracing.py:loop_facts` over every harness's trace. That function counts
`reply` records as model turns and `command` records as executed actions -- so a harness
that writes neither is reported to the method as `0 steps, 0 commands`, whatever it
actually did. Measured on the two shapes:

    codex-style  (harness_identity + codex_event + usage)  -> steps 0, commands 0
    miniswe-style (harness_identity + step/command + usage) -> steps 0, commands 2

The first line is the defect this file exists to make visible: an adapter that works, is
measured, and reports to the method that it did nothing.

What it does not check
----------------------
Whether the numbers are *good* -- only whether the record says what the platform reads.
A trace can pass every clause here and still be a weak harness; it cannot be weak and pass
the clause that says the platform will see its turns.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: The one record that every trace must carry, because it is how a curve point learns which
#: model actually ran (`eval/observe/tracing.py:_last_identity`, last one wins).
IDENTITY = "harness_identity"

#: What the platform counts. Two keys, both read by `loop_facts`.
TURN_KEYS = ("reply",)
ACTION_KEYS = ("command",)

#: The environment's answer to an action. `returncode` is the one field that exists in every
#: environment kind (`0` from a shell, an HTTP status from an API, `null` where the
#: environment reports nothing) and is what turns "the harness did something" into "the
#: environment said something back".
OBSERVATION = "observation"


def records(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def dialect_of(rows: list[dict]) -> str:
    """Which of the two trace dialects this is.

    `v1` is `docs/adapter-spec.md`: one record per action, the environment's answer under
    `observation`. `v0` is what this platform's own `base_harness/loop` writes and what
    every trace stored so far contains: the same answer inline, as `exit`/`output`.

    The reader learned v0 first (`loop_facts`), because a contract that made the whole
    history unreadable would be a report about the future. What v0 does not give is
    portability: `exit` is a shell field, so a document-retrieval environment has nowhere
    to put its status.
    """
    if any(isinstance(row.get("observation"), dict) for row in rows):
        return "v1"
    if any("command" in row and ("exit" in row or "returncode" in row) for row in rows):
        return "v0"
    return "v1"        # nothing to tell them apart: hold it to the newer contract


def inspect(text: str) -> dict:
    """`{"dialect", "problems", "notes"}` -- what the platform will and will not see."""
    rows = records(text)
    problems: list[str] = []
    notes: list[str] = []
    dialect = dialect_of(rows)
    if not rows:
        return {"dialect": dialect, "problems": ["the trace has no parsable JSON records at "
                                                 "all"], "notes": notes}

    # 1. Identity. Without it a curve point records a model that did not run.
    identity = [r[IDENTITY] for r in rows if isinstance(r.get(IDENTITY), dict)]
    if not identity:
        problems.append("no `harness_identity` record: the curve point cannot say which "
                        "model ran")
    elif not identity[-1].get("harness"):
        problems.append("`harness_identity` has no `harness` field")

    # 2. Usage, last one wins. Absent means the platform records no call count, and
    #    `ended` reads it as a crash -- which is why a missing `usage` is a violation
    #    rather than a zero.
    usage = [r["usage"] for r in rows if isinstance(r.get("usage"), dict)]
    calls = None
    if not usage:
        problems.append("no `usage` record: the platform cannot tell a crash from a run "
                        "that finished without reporting")
    else:
        calls = usage[-1].get("calls")
        if not isinstance(calls, int):
            problems.append(f"`usage.calls` is {calls!r}, not an integer")

    # 3. Turns. `loop_facts.model_calls` counts `reply` records, not the API's own count,
    #    so a non-zero `calls` with no turn records is reported to the method as 0 steps.
    turns = sum(1 for r in rows if any(key in r for key in TURN_KEYS))
    if isinstance(calls, int) and calls > 0 and turns == 0:
        problems.append(f"`usage.calls` is {calls} but no record carries "
                        f"{'/'.join(TURN_KEYS)}: the method is told the harness took 0 "
                        f"steps")

    # 4. Feedback. Every executed action must carry the environment's answer in a shape
    #    that reads the same for every environment kind -- or a method cannot read
    #    observations across harnesses, which is the point of a shared trace.
    actions = [r for r in rows if any(key in r for key in ACTION_KEYS)]
    if actions and dialect == "v1":
        if not any(isinstance(r.get("observation"), dict) for r in rows):
            problems.append(f"{len(actions)} action record(s) and no `{OBSERVATION}`: the "
                            f"environment's answer is not in the trace in a readable form")
        else:
            for row in actions:
                observation = row.get(OBSERVATION)
                if not isinstance(observation, dict):
                    problems.append("an action record carries no `observation` object")
                    break
                if "returncode" not in observation:
                    problems.append("an `observation` has no `returncode` key")
                    break
    elif actions:
        inline = [r for r in actions if "exit" in r or "returncode" in r]
        if not inline:
            problems.append(f"{len(actions)} action record(s) and no `exit`/`returncode`: "
                            f"the environment's answer is not in the trace at all")
        else:
            notes.append("v0 dialect: the environment's answer is inline (`exit`/`output`), "
                         "which the platform reads but a non-shell environment cannot use")

    # 4b. One record, one fact. `loop_facts` counts every record carrying `reply` as a model
    #     turn, so a record that carries both `reply` and `command` is counted twice: once as
    #     a turn and once as an action. Measured on the first shape of this platform's own
    #     miniswe adapter: three commands in one turn reported `model_calls: 4`.
    combined = [r for r in rows if "reply" in r and "command" in r]
    if combined:
        problems.append(f"{len(combined)} record(s) carry both `reply` and `command`: the "
                        f"platform counts each as a model turn *and* as an action, so "
                        f"`model_calls` is inflated")

    # 5. A terminal record. Without one, `loop_facts` has to infer how the run ended, and
    #    a harness that answered and one that was killed look the same.
    terminal = any(r.get("submitted") or r.get("error") for r in rows)
    if not terminal:
        if dialect == "v1":
            problems.append("no terminal record (`submitted` or `error`): how the run ended "
                            "cannot be read")
        else:
            notes.append("v0 dialect: no terminal record; `ended` is inferred from `usage` "
                         "and the step records")
    return {"dialect": dialect, "problems": problems, "notes": notes}


def check(text: str) -> list[str]:
    """The problems only; `[]` means the trace says what the platform reads."""
    return inspect(text)["problems"]


def _traces(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(target.rglob("trace.jsonl"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", type=Path, help="a trace.jsonl, or a directory to search")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    found = _traces(args.target)
    if not found:
        print(f"no trace.jsonl under {args.target}", file=sys.stderr)
        return 2
    report = {str(path): inspect(path.read_text(encoding="utf-8", errors="replace"))
              for path in found}
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        for path, result in report.items():
            problems, notes = result["problems"], result["notes"]
            print(f"{'FAIL ' if problems else 'OK   '} {path}  [{result['dialect']}]")
            for problem in problems:
                print(f"      - {problem}")
            for note in notes:
                print(f"      . {note}")
    return 1 if any(r["problems"] for r in report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
