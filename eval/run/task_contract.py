"""Adapter-declared, public runtime instructions shared by all harnesses."""
from __future__ import annotations

#: The file the platform collects a text answer from (`eval/run/runner.py`), and the one
#: every verifier written against an `answer`-kind task is given.
ANSWER_FILE = "answer.txt"

#: What an `answer`-kind task tells the agent, when it does not say it itself.
#:
#: **This is the task's requirement, not the harness's.** The platform owns the verifier,
#: so the platform is the party that knows the answer has to arrive in `answer.txt`; the
#: harness only has to leave alone what the task asked for. Putting the sentence here means
#: one instruction for every harness instead of six adapters each inventing a channel --
#: measured 2026-11-01: naming the channel inside the adapters made two of them score 0 for
#: reasons the harness had created, and the same sentence in each adapter is six places to
#: drift. Datasets that grade on their own command (AppWorld, Terminal-Bench, the QA
#: scaffold, ...) are untouched: their goal already says what to deliver, or their verifier
#: never reads an answer.
DELIVERY = ("## How to answer\n"
            "\n"
            f"Write the answer, and nothing else, to `{ANSWER_FILE}` in your working "
            "directory.\n")


def runtime_task(task: dict, setup: dict | None, verdict_kind: str | None = None) -> dict:
    """Render the public task instructions into `goal` without mutating the task.

    Only explicitly public fields cross this boundary. Fixture source paths and verifier
    metadata remain platform-owned; a harness that reads only ``goal`` receives the
    instructions without needing an adapter-specific parser.

    ``verdict_kind`` is the task's verifier kind. For ``answer`` the delivery instruction
    above is appended unless the goal already names the file -- a dataset that wrote its own
    "How to answer" section keeps its wording, and one that graded on files or on the
    environment gets nothing.
    """
    goal = str(task.get("goal") or "")
    if setup and "runtime_prompt" in setup:
        prompt = setup["runtime_prompt"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("SETUP.runtime_prompt must be a nonempty string")
        goal = goal + "\n\nRuntime tool instructions:\n" + prompt
    if verdict_kind == "answer" and ANSWER_FILE not in goal:
        goal = (goal + ("\n\n" if goal else "")) + DELIVERY
    if goal == str(task.get("goal") or ""):
        return task
    return {**task, "goal": goal}
