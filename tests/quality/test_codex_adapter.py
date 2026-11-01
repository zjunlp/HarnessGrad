"""The Codex adapter translates `codex exec --json` into the contract's records.

Codex's stream is its own: `item.completed` carries `item.type` of `command_execution`
(`command`, `aggregated_output`, `exit_code`), `agent_message` (`text`) or `file_change`;
`turn.completed` closes a model turn and carries `usage`. The platform's one extractor counts
`reply` and `command`, so before this translation a Codex run that issued forty commands was
reported to the method as `0 steps, 0 commands` -- the motivating measurement in
`tests/quality/test_trace_contract.py`.

Two properties are pinned here because they are easy to get wrong and invisible when wrong:
**one record carries one fact** (a record with both `reply` and `command` is counted as a
turn *and* an action), and the raw event survives beside the translation, so a field the
contract does not name is still readable.
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

HARNESS = ROOT / "base_harness" / "codex"


@pytest.fixture(scope="module")
def codex():
    spec = importlib.util.spec_from_file_location("_codex_agent", HARNESS / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _feed(codex, events) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    collector = codex.CodexTrace(rows.append)
    collector.feed("\n".join(event if isinstance(event, str) else json.dumps(event)
                             for event in events))
    facts = loop_facts("\n".join(json.dumps(row) for row in rows))
    return rows, facts


def test_a_command_becomes_a_command_and_an_observation(codex):
    rows, facts = _feed(codex, [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "looking"}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "ls -a",
                                            "aggregated_output": "a b", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "cat x",
                                            "aggregated_output": "no such file",
                                            "exit_code": 1}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}},
    ])
    actions = [row for row in rows if "command" in row]
    assert [row["command"] for row in actions] == ["ls -a", "cat x"]
    assert actions[0]["observation"] == {"kind": "shell", "returncode": 0,
                                         "output_chars": 3, "truncated": False}
    assert actions[1]["observation"]["returncode"] == 1
    assert (facts["commands"], facts["observations"]) == (2, 2)
    assert facts["return_codes"] == {"0": 1, "1": 1}


def test_a_turn_is_one_record_and_actions_are_not_turns(codex):
    """A record carrying both `reply` and `command` is counted twice; the first shape of this
    adapter did exactly that and reported `model_calls: 4` for one turn with three commands."""
    rows, facts = _feed(codex, [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "a"}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "ls",
                                            "aggregated_output": "", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "pwd",
                                            "aggregated_output": "/", "exit_code": 0}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ])
    assert facts["model_calls"] == 1, rows
    assert facts["commands"] == 2
    assert not [row for row in rows if "reply" in row and "command" in row]
    usage = [row["usage"] for row in rows if "usage" in row][-1]
    assert usage["calls"] == 1


def test_a_patch_is_named_for_what_it_is(codex):
    """`file_change` is Codex's own apply-patch tool, not a shell line: naming it as a
    command it never ran would make the trace read as if a shell had executed something."""
    rows, facts = _feed(codex, [
        {"type": "item.completed", "item": {"type": "file_change",
                                            "changes": [{"path": "a.txt", "kind": "add"}],
                                            "status": "completed"}},
    ])
    action = next(row for row in rows if "command" in row)
    assert action["command"] == codex.PATCH_ACTION
    assert action["observation"]["kind"] == "filesystem"
    assert action["observation"]["returncode"] is None
    assert facts["commands"] == 1


def test_the_raw_event_survives_beside_the_translation(codex):
    event = {"type": "item.completed",
             "item": {"type": "command_execution", "command": "ls", "exit_code": 0,
                      "duration_ms": 12, "process_id": 42}}
    rows, _ = _feed(codex, [event])
    assert {"codex_event": event} in rows, "a field the contract does not name is still there"


def test_a_malformed_line_is_kept_and_does_not_stop_the_stream(codex):
    rows, facts = _feed(codex, [
        "not json at all",
        {"type": "item.completed", "item": {"type": "command_execution", "command": "ls",
                                            "aggregated_output": "", "exit_code": 0}},
    ])
    assert rows[0] == {"codex_event_raw": "not json at all"}
    assert facts["commands"] == 1


def test_the_manifest_still_conforms(codex):
    spec = importlib.util.spec_from_file_location(
        "_validate_harness", ROOT / "tools" / "validate_harness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.validate(HARNESS, smoke=False) == []
