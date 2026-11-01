"""DSH's session log is its structured output, and this is the translation.

`deepseek-harness` streams human text on two pipes -- reasoning to stderr, the final message
to stdout -- and publishes per-step structure only in the session log it flushes on exit
(`packages/bundle/headless/src/index.ts`, `sessions.flush`). So the adapter that read only
the pipes could say "one turn, this answer" and nothing about what the agent *did*; every
`tool/call` and `tool/result` was already on disk, unread.

The event shapes below are copied from a real log on this host (330 events, 98 tool calls,
29 assistant messages, each carrying `usage`), not from the type declarations: a translator
written against the declarations would have missed that a **tool result nests its text one
level deeper** than an assistant message, which reads as an environment that answered with
zero characters.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.observe.tracing import loop_facts                                   # noqa: E402

HARNESS = ROOT / "base_harness" / "deepseek-harness"


@pytest.fixture(scope="module")
def dsh():
    spec = importlib.util.spec_from_file_location("_dsh_session", HARNESS / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _events(tool: str = "bash", *, arguments: str = '{"command": "ls"}',
            result_text: str = "", exit_code: int | None = 0) -> list[dict]:
    """One turn with one tool call, in the shapes a real log uses."""
    meta = {} if exit_code is None else {"meta": {"exitCode": exit_code}}
    return [
        {"type": "session", "version": 3, "id": "s", "cwd": "/tmp"},
        {"type": "turn/start", "seq": 1, "data": {"turn": 1}},
        {"type": "step/start", "seq": 2, "data": {"turn": 1, "step": 1}},
        {"type": "assistant/message", "seq": 3, "data": {
            "turn": 1, "step": 1,
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "I will run it."},
                {"type": "tool-call", "id": "call_1", "name": tool,
                 "arguments": arguments}]},
            "usage": {"inputTokens": 120, "outputTokens": 8, "cacheReadTokens": 40}}},
        {"type": "tool/call", "seq": 4, "data": {
            "turn": 1, "step": 1, "callId": "call_1", "name": tool,
            "arguments": arguments}},
        # The nesting that a declaration-shaped reader gets wrong: the text lives inside
        # `content[0].content[0].text`, not `content[0].text`.
        {"type": "tool/result", "seq": 5, "data": {
            "turn": 1, "step": 1,
            "message": {"source": {"kind": "tool", "callId": "call_1"},
                        "content": [{"type": "tool-result", "toolCallId": "call_1",
                                     "content": [{"type": "text", "text": result_text}]}]},
            **meta}},
        {"type": "step/end", "seq": 6, "data": {"turn": 1, "step": 1}},
        {"type": "turn/end", "seq": 7, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]


def _translate(dsh, events):
    rows: list[dict] = []
    summary = dsh._record_session(events, rows.append)
    return rows, summary, loop_facts("\n".join(json.dumps(row) for row in rows))


def test_a_turn_and_its_tool_call_become_the_contracts_records(dsh):
    rows, summary, facts = _translate(dsh, _events(result_text="a b"))
    assert [row["reply"] for row in rows if "reply" in row] == ["I will run it."]
    action = next(row for row in rows if "command" in row)
    assert action["command"].startswith("bash ")
    assert action["observation"]["kind"] == "shell"
    assert action["observation"]["returncode"] == 0
    assert action["observation"]["output_chars"] == 3, "the nested tool-result text"
    assert (facts["model_calls"], facts["commands"], facts["observations"]) == (1, 1, 1)
    assert summary["turn_end"] == "completed"


def test_usage_is_real_and_calls_matches_the_turns(dsh):
    """The adapter had no token counts at all before this: `usage` came from nothing."""
    rows, _, _ = _translate(dsh, _events())
    usage = [row["usage"] for row in rows if "usage" in row][-1]
    assert usage == {"input_tokens": 120, "output_tokens": 8,
                     "cached_input_tokens": 40, "calls": 1}


def test_the_command_carries_its_arguments_so_calls_are_not_counted_as_repeats(dsh):
    """`loop_facts` treats two equal `command` strings as a re-issue; a bare tool name would
    make every `web_fetch` look like a repeat of the previous one."""
    first = _events("web_fetch", arguments='{"url": "https://a"}')
    second = _events("web_fetch", arguments='{"url": "https://b"}')
    rows = []
    dsh._record_session(first + second, rows.append)
    commands = [row["command"] for row in rows if "command" in row]
    assert len(set(commands)) == 2, commands
    assert loop_facts("\n".join(json.dumps(r) for r in rows))["repeated_commands"] == 0


def test_a_tool_without_an_exit_code_is_null_not_zero(dsh):
    """`web_fetch` reports no exit code; writing `0` would make every failed fetch look
    successful to a method that reads return codes."""
    _, _, facts = _translate(dsh, _events("web_fetch", arguments='{"url": "x"}',
                                          result_text="HTTP 500", exit_code=None))
    assert facts["return_codes"] == {"None": 1}
    assert facts["nonzero_returns"] == 0


def test_a_failed_tool_result_keeps_its_error(dsh):
    events = _events(result_text="boom")
    events[5]["data"]["error"] = {"name": "ToolError", "code": "E_FAIL"}
    rows, _, _ = _translate(dsh, events)
    action = next(row for row in rows if "command" in row)
    assert "E_FAIL" in action["error"]


def test_the_newest_log_under_the_home_is_the_one_read(dsh, tmp_path):
    """The directory is bucketed by a hash of the cwd; searching by time rather than
    rebuilding the key is what keeps a spelling change from producing an empty trace."""
    root = tmp_path / ".dsh" / "sessions" / "--bucket-a--" / "old"
    newer = tmp_path / ".dsh" / "sessions" / "--bucket-b--" / "new"
    root.mkdir(parents=True)
    newer.mkdir(parents=True)
    (root / "session.v3.jsonl").write_text("{}\n")
    time.sleep(0.01)
    (newer / "session.v3.jsonl").write_text("{}\n")
    found = dsh.find_session_log(tmp_path, time.time() - 5)
    assert found == newer / "session.v3.jsonl"
    assert dsh.find_session_log(tmp_path, time.time() + 60) is None, \
        "a log older than the run is not this run's"


@pytest.mark.parametrize("compress", [False, True])
def test_the_log_is_read_compressed_or_not(dsh, tmp_path, compress):
    log = tmp_path / "session.v3.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in _events()) + "\n")
    if compress:
        if not shutil.which("zstd"):
            pytest.skip("no zstd binary on this machine")
        target = Path(str(log) + ".zstd")
        subprocess.run(["zstd", "-q", "-f", str(log), "-o", str(target)], check=True)
        log.unlink()
        log = target
    events, how = dsh.read_session_log(log)
    assert len(events) == len(_events())
    assert how in ("plain", "zstandard", "zstd-cli")


def test_an_unknown_event_shape_yields_nothing_rather_than_a_wrong_something(dsh):
    """The format is versioned (`session.v3`). A future shape must be visibly untranslated,
    which is why the caller records `translated: false` and falls back to the text streams."""
    rows, summary, _ = _translate(dsh, [{"type": "session", "version": 9},
                                        {"type": "brand/new", "data": {}}])
    assert rows == []
    assert summary["turns"] == 0 and summary["actions"] == 0
