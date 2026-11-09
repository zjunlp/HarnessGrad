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
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.observe.tracing import loop_facts                                   # noqa: E402
from tools.configure_codex_harness import configure                           # noqa: E402

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


def test_subagent_usage_is_not_misreported_as_parent_total(codex):
    rows, facts = _feed(codex, [
        {"type": "item.started", "item": {"type": "collab_tool_call", "tool": "spawn_agent"}},
        {"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "spawn_agent",
                                                  "receiver_thread_ids": ["child"]}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}},
    ])
    usage = [row['usage'] for row in rows if 'usage' in row][-1]
    assert usage['input_tokens'] is None and usage['output_tokens'] is None
    assert usage['parent_thread']['input_tokens'] == 100
    assert usage['scope'] == 'subagent_usage_incomplete'
    assert usage['calls'] == facts['model_calls'] == 1


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


# --------------------------------------------------- who owns `answer.txt` ---
#
# The adapter used to hand Codex `-o <workdir>/answer.txt`, after deleting that file, so
# Codex's final message *became* the answer whether or not the task asked for a file. A
# dataset's goal is what asks for an answer (gsm8k's own goal names `answer.txt`), and the
# model narrates before answering, so the two cases have to be told apart rather than
# merged. These two tests pin the precedence with a stand-in CLI, because the real binary
# cannot be a unit test's dependency.

FAKE_CODEX = '''\
import json, os, sys
argv = sys.argv[1:]
if argv == ['--version']:
    print('codex-cli answer-fixture')
    raise SystemExit(0)
if os.environ.get('HG_AGENT_EXPECT_CODEX_ISOLATION'):
    assert not any(key.startswith(('OPENAI_', 'CODEX_', 'HG_METHOD_', 'HG_ENV_', 'HG_JUDGE_'))
                   and key != 'CODEX_HOME' for key in os.environ)
    assert os.environ['HG_AGENT_API_KEY'] == 'task-fixture-key'
    assert os.environ['CODEX_HOME'] != os.environ['HG_AGENT_PERSONAL_HOME_FIXTURE']
target = argv[argv.index("-o") + 1] if "-o" in argv else None
sys.stdin.read()
if os.environ.get("FAKE_WRITES_ANSWER"):
    open("answer.txt", "w").write("42")
if target:
    open(target, "w").write("The user wants 17*23.\\n\\n\\n391")
print(json.dumps({"type": "item.completed",
                  "item": {"id": "i0", "type": "agent_message", "text": "done"}}))
print(json.dumps({"type": "turn.completed",
                  "usage": {"input_tokens": 10, "output_tokens": 2}}))
'''


def _run_with_fake(codex, tmp_path, monkeypatch, *, writes_answer: bool):
    if shutil.which("bwrap") is None:
        pytest.skip("preparing Codex requires bubblewrap")
    with tempfile.TemporaryDirectory(prefix="hg-codex-answer-", dir="/var/tmp") as directory:
        fake = Path(directory) / "fake-codex.py"
        fake.write_text(FAKE_CODEX)
        harness = tmp_path / "candidate"
        shutil.copytree(HARNESS, harness)
        raw = shlex.join([sys.executable, str(fake)])
        configure(harness, command=raw, env={"PATH": "/usr/bin:/bin"})
        monkeypatch.setattr(codex, "__file__", str(harness / "agent.py"))
        monkeypatch.setenv("HG_AGENT_CODEX_COMMAND", raw)
        monkeypatch.setenv("HG_AGENT_MODEL", "fake-model")
        monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
        if writes_answer:
            monkeypatch.setenv("FAKE_WRITES_ANSWER", "1")
        work = tmp_path / "work"
        work.mkdir()
        assert codex.run({"goal": "write the answer"}, work) == 0
        rows = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
        submitted = next(row for row in rows if row.get("submitted"))
        return submitted, (work / "answer.txt").read_text()


def test_a_task_written_answer_txt_is_kept_and_wins(codex, tmp_path, monkeypatch):
    submitted, on_disk = _run_with_fake(codex, tmp_path, monkeypatch, writes_answer=True)
    assert submitted["answer_source"] == "artifact"
    assert submitted["answer"] == "42"
    assert on_disk == "42", "the task's own deliverable is not overwritten by the CLI"


def test_the_final_message_is_the_fallback_when_the_task_wrote_nothing(
        codex, tmp_path, monkeypatch):
    submitted, on_disk = _run_with_fake(codex, tmp_path, monkeypatch, writes_answer=False)
    assert submitted["answer_source"] == "final_message"
    assert submitted["answer"].endswith("391")
    assert on_disk == submitted["answer"]


def test_personal_codex_settings_and_other_role_credentials_are_not_inherited(codex, tmp_path, monkeypatch):
    for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_PROFILE', 'HG_METHOD_API_KEY',
                'HG_ENV_API_KEY', 'HG_JUDGE_API_KEY'):
        monkeypatch.setenv(key, 'synthetic-personal-value')
    personal_home = str(tmp_path / 'personal-codex')
    monkeypatch.setenv('CODEX_HOME', personal_home)
    monkeypatch.setenv('HG_AGENT_PERSONAL_HOME_FIXTURE', personal_home)
    monkeypatch.setenv('HG_AGENT_API_KEY', 'task-fixture-key')
    monkeypatch.setenv('HG_AGENT_EXPECT_CODEX_ISOLATION', '1')
    submitted, _ = _run_with_fake(codex, tmp_path, monkeypatch, writes_answer=False)
    assert submitted['submitted'] is True
