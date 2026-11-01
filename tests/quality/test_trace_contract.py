"""The adapter contract is checked by code, not by prose.

`docs/adapter-spec.md` says what an adapter must write; `tools/check_trace_contract.py`
says whether a trace does. Both halves exist because the failure they describe is silent:
the platform reads every harness's trace with **one** extractor (`loop_facts`), it counts
`reply` records as model turns and `command` records as executed actions, and a harness that
writes neither is reported to the method as `0 steps, 0 commands` while it ran a full task.

The traces below are the two shapes that were measured, plus one good record:
`codex_event` (a CLI harness's own event stream) and `command` (what the platform counts).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.observe.tracing import loop_facts                                   # noqa: E402


def _tool(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _tool("_check_trace_contract", ROOT / "tools" / "check_trace_contract.py")


def _trace(rows: list[dict]) -> str:
    return "\n".join(json.dumps(row) for row in rows)


IDENTITY = {"harness_identity": {"harness": "h", "agent_model": "m"}, "task_id": "t"}


def _good() -> list[dict]:
    return [IDENTITY,
            {"step": 1, "reply": "ls", "command": "ls",
             "observation": {"kind": "shell", "returncode": 0}},
            {"step": 2, "reply": "cat f", "command": "cat f",
             "observation": {"kind": "shell", "returncode": 1}},
            {"usage": {"calls": 2, "input_tokens": 10, "output_tokens": 2},
             "submitted": True, "answer": "x"}]


# ----------------------------------------------------------------- the contract ---

def test_a_conforming_trace_passes(checker):
    assert checker.check(_trace(_good())) == []


def test_a_cli_harness_that_writes_only_its_own_events_is_caught(checker):
    """The measured defect: a working agent reported to the method as doing nothing."""
    codex_like = [IDENTITY,
                  {"codex_event": {"type": "item.completed",
                                   "item": {"command": "ls"}}},
                  {"usage": {"calls": 2, "input_tokens": 10, "output_tokens": 2},
                   "submitted": True}]
    problems = checker.check(_trace(codex_like))
    assert any("0 steps" in p for p in problems), problems
    facts = loop_facts(_trace(codex_like))
    assert (facts["model_calls"], facts["commands"]) == (0, 0), \
        "if this ever stops being true, the contract's motivating measurement is stale"


def test_the_same_run_in_the_canonical_shape_is_counted(checker):
    """The other half: same work, canonical records, and the platform sees it."""
    facts = loop_facts(_trace(_good()))
    assert (facts["model_calls"], facts["commands"]) == (2, 2)
    assert facts["observations"] == 2
    assert facts["nonzero_returns"] == 1
    assert facts["return_codes"] == {"0": 1, "1": 1}


@pytest.mark.parametrize("mutate, needle", [
    (lambda rows: [r for r in rows if "harness_identity" not in r], "identity"),
    (lambda rows: [r for r in rows if "usage" not in r], "usage"),
    (lambda rows: rows[:-1], "terminal"),
])
def test_each_clause_has_a_failure_that_names_it(checker, mutate, needle):
    problems = checker.check(_trace(mutate(_good())))
    assert problems and any(needle in p.lower() for p in problems), problems


def test_an_action_without_the_environments_answer_is_caught(checker):
    rows = _good()
    rows[1] = {"step": 1, "reply": "ls", "command": "ls"}          # observation dropped
    problems = checker.check(_trace(rows))
    assert any("observation" in p for p in problems), problems


def test_an_observation_without_a_returncode_is_caught(checker):
    rows = _good()
    rows[1] = {**rows[1], "observation": {"kind": "shell", "output_chars": 3}}
    problems = checker.check(_trace(rows))
    assert any("returncode" in p for p in problems), problems


def test_a_usage_count_that_is_not_an_int_is_caught(checker):
    rows = _good()
    rows[-1] = {**rows[-1], "usage": {"calls": "two"}}
    assert any("integer" in p for p in checker.check(_trace(rows)))


def test_an_empty_trace_is_not_a_pass(checker):
    assert checker.check("") and checker.check("\n\n")


# --------------------------------------------------------- the bases that conform ---

def test_miniswe_writes_the_canonical_feedback(capsys):
    """The first adapter that emits `observation`; a later one can copy it exactly."""
    trajectory = Path("/tmp") / "hg-contract-trajectory.json"
    trajectory.write_text(json.dumps({
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "extra": {
                "actions": [{"command": "ls"}],
                "response": {"usage": {"prompt_tokens": 3, "completion_tokens": 1}}}},
            {"role": "tool", "content": "{}", "extra": {
                "returncode": 0, "raw_output": "a b", "exception_info": ""}},
        ],
        "info": {"model_stats": {"api_calls": 1},
                 "mini_version": "2.4.6", "exit_status": "Submitted"},
    }), encoding="utf-8")
    agent = _tool("_miniswe_agent_contract",
                  ROOT / "base_harness" / "miniswe" / "agent.py")
    rows: list[dict] = []
    agent.write_trace(trajectory, rows.append)
    steps = [r for r in rows if "command" in r]
    assert steps and steps[0]["observation"] == {
        "kind": "shell", "returncode": 0, "output_chars": 3, "truncated": False}
    facts = loop_facts(_trace(rows))
    assert facts["observations"] == 1 and facts["commands"] == 1


# ------------------------------------------------------- the v0 dialect (`loop`) ---

def _loop_shaped(exit_code: int = 0, terminal: bool = False) -> list[dict]:
    """What `base_harness/loop` actually writes: the answer inline as `exit`/`output`."""
    rows = [{"harness_identity": {"harness": "loop", "agent_backend": "mock"}},
            {"step": 0, "reply": '{"tool": "bash", "command": "ls -a"}', "model_ms": 0},
            {"step": 0, "command": "ls -a", "exit": exit_code,
             "shell": "/usr/bin/bash", "output": "a b"}]
    if terminal:
        rows.append({"step": 1, "submitted": True, "answer": "x"})
    rows.append({"usage": {"calls": 2, "input_tokens": 10, "output_tokens": 2}})
    return rows


def test_the_v0_dialect_is_read_not_rejected(checker):
    """Every stored trace is v0. A reader that could not read them would make the whole
    history unreadable, so the platform reads both and the checker says which one it saw."""
    rows = _loop_shaped(exit_code=1, terminal=True)
    result = checker.inspect(_trace(rows))
    assert result["dialect"] == "v0"
    assert result["problems"] == [], result["problems"]
    assert any("inline" in note for note in result["notes"]), result["notes"]

    facts = loop_facts(_trace(rows))
    assert (facts["commands"], facts["observations"], facts["nonzero_returns"]) == (1, 1, 1)
    assert facts["return_codes"] == {"1": 1}


def test_a_v0_trace_without_any_status_is_still_a_problem(checker):
    rows = _loop_shaped()
    rows[2] = {"step": 0, "command": "ls -a", "output": "a b"}     # no `exit`
    problems = checker.check(_trace(rows))
    assert problems, "a run that executed commands with no status at all is not a pass"
    assert any("observation" in p or "exit" in p or "returncode" in p for p in problems)


def test_a_v0_trace_with_no_terminal_record_is_a_note_not_a_failure(checker):
    """`loop` writes `submitted` only when it submits; running out of steps is a normal end
    and `_ended` infers it. Holding the reference harness to a record it does not need would
    report every budget-exhausted run as a violation."""
    result = checker.inspect(_trace(_loop_shaped(terminal=False)))
    assert result["problems"] == []
    assert any("terminal" in note for note in result["notes"])

