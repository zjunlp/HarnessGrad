"""Adapter-declared, public runtime instructions shared by all harnesses."""
from __future__ import annotations

import json

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
    is_files = isinstance(task.get("submission"), dict) and task["submission"].get("kind") == "files"
    if verdict_kind == "answer" and not is_files and ANSWER_FILE not in goal:
        goal = (goal + ("\n\n" if goal else "")) + DELIVERY
    if goal == str(task.get("goal") or ""):
        return task
    return {**task, "goal": goal}


def cli_task_text(task: dict) -> str:
    """Render only public task content for CLI adapters, consistently across harnesses."""
    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task requires a non-empty goal or instruction")
    result = goal
    if task.get("inputs") is not None:
        result += "\n\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
    spec = task.get("submission")
    if "submission" in task and spec != {"kind": "text"}:
        if not isinstance(spec, dict) or set(spec) != {"kind", "paths"} or spec["kind"] != "files":
            raise ValueError("invalid public submission descriptor")
        paths = spec["paths"]
        if not isinstance(paths, list) or not paths or any(
            not isinstance(p, str) or not p.strip() or "\\" in p or ":" in p
            or any(part in ("", ".", "..") for part in p.split("/")) for p in paths
        ):
            raise ValueError("file submission paths must be relative paths within the workdir")
        result += ("\n\nTask delivery:\nLeave these files in the working directory: "
                   + json.dumps(paths, ensure_ascii=False)
                   + ". A final message or answer.txt is not required in addition to the files.")
    return result


def with_cli_task(task: dict) -> dict:
    """Bind platform-rendered CLI content, overriding any task-supplied value."""
    return {**task, "cli_task": cli_task_text(task)}
