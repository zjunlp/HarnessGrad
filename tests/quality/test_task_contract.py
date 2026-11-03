"""Who tells the agent where the answer goes: the task, not the harness.

The platform owns the verifier, so it is the party that knows an `answer`-kind task is
graded by comparing `answer.txt` with `expected`. That instruction therefore belongs in the
task's own text (`runtime_task`), where every harness reads it, instead of in six adapters
each naming a channel. Measured 2026-11-01: the harness-side version was six places to
drift, and it made two adapters score 0 for reasons the harness had created.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.run.task_contract import ANSWER_FILE, runtime_task                  # noqa: E402


def test_an_answer_graded_task_is_told_where_the_answer_goes():
    task = {"task_id": "t01", "goal": "Answer with the word alpha."}
    rendered = runtime_task(task, None, verdict_kind="answer")
    assert "## How to answer" in rendered["goal"]
    assert f"`{ANSWER_FILE}`" in rendered["goal"]
    assert rendered["goal"].startswith("Answer with the word alpha.")


def test_a_task_that_says_it_itself_keeps_its_own_wording():
    """gsm8k and every QA adapter ship a `## How to answer` section already."""
    goal = ("# Task\n\nSolve this.\n\n## How to answer\n\nWrite the answer to "
            f"`{ANSWER_FILE}`.")
    rendered = runtime_task({"goal": goal}, None, verdict_kind="answer")
    assert rendered["goal"] == goal


def test_a_command_graded_task_gets_nothing_added():
    """AppWorld, Terminal-Bench and the QA scaffold are graded by their own command; the
    answer file is not the deliverable and telling the agent to write one invites it to
    stop early."""
    task = {"goal": "Fix broken.py so python3 test_broken.py exits 0."}
    assert runtime_task(task, None, verdict_kind="command") == task


def test_the_task_dict_is_not_mutated():
    task = {"task_id": "t01", "goal": "Answer with the word alpha."}
    runtime_task(task, None, verdict_kind="answer")
    assert task == {"task_id": "t01", "goal": "Answer with the word alpha."}


def test_a_dataset_runtime_prompt_still_arrives_and_the_delivery_follows_it():
    setup = {"runtime_prompt": "Act through the app's own HTTP API."}
    rendered = runtime_task({"goal": "Book the flight."}, setup, verdict_kind="answer")
    assert "Runtime tool instructions:" in rendered["goal"]
    assert rendered["goal"].index("Runtime tool instructions:") < rendered["goal"].index(
        "## How to answer")


def test_an_empty_goal_does_not_begin_with_blank_lines():
    rendered = runtime_task({}, None, verdict_kind="answer")
    assert rendered["goal"].startswith("## How to answer")


def test_an_empty_runtime_prompt_is_refused():
    with pytest.raises(ValueError):
        runtime_task({"goal": "x"}, {"runtime_prompt": "  "}, verdict_kind="answer")
