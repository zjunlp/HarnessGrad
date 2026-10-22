"""Generic loop regressions: synthetic commands/data only, no benchmark access."""
from __future__ import annotations

import copy
import importlib.util
import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def agent(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "synthetic_robust_loop", ROOT / "base_harness/loop/agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MAX_STEPS", 8)
    monkeypatch.setattr(module, "FINISH_NUDGE_STEPS", 0)
    return module


def action(command):
    return json.dumps({"tool": "bash", "command": command})


def scripted(agent, monkeypatch, tmp_path, replies):
    seen = []

    def complete(messages):
        seen.append(copy.deepcopy(messages))
        return replies[len(seen) - 1]

    monkeypatch.setattr(agent, "_complete", complete)
    result = agent.run({"goal": "Inspect synthetic data and report."}, tmp_path)
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    return result, seen, events


def test_bash_contract_executes_arrays_and_pipeline_status_once(agent, monkeypatch, tmp_path):
    if not shutil.which("bash"):
        pytest.skip("Bash is not installed")
    command = ("printf 'once\\n' >> invocations; false | true; "
               "status=${PIPESTATUS[0]}; values=(a b); "
               "[[ $status == 1 && ${values[1]} == b ]] && printf supported")
    result, seen, events = scripted(agent, monkeypatch, tmp_path,
                                    [action(command), '{"answer":"done"}'])
    executed = next(e for e in events if "command" in e)
    assert result == "done" and executed["exit"] == 0
    assert executed["output"] == "supported"
    assert (tmp_path / "invocations").read_text() == "once\n"
    config = events[0]["harness_identity"]["config"]
    assert config["shell"] == executed["shell"] == shutil.which("bash")
    assert config["shell_dialect"] == "Bash"
    assert f"{config['shell']} -c (Bash)" in seen[0][0]["content"]


def test_posix_fallback_is_explicit_and_never_reexecutes_failed_side_effects(
        agent, monkeypatch, tmp_path):
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    command = "printf 'once\\n' >> invocations; echo ${PIPESTATUS[0]}"
    _, seen, events = scripted(agent, monkeypatch, tmp_path,
                              [action(command), '{"answer":"done"}'])
    executed = next(e for e in events if "command" in e)
    assert executed["shell"] == "/bin/sh" and executed["exit"] != 0
    assert (tmp_path / "invocations").read_text() == "once\n"
    assert "/bin/sh -c (POSIX sh)" in seen[0][0]["content"]
    assert "PIPESTATUS and [[ ]] are unavailable" in seen[0][0]["content"]


def test_structured_result_is_compacted_without_losing_middle_records(agent, monkeypatch):
    records = [{"id": i, "name": f"operation_{i}", "description": "synthetic details"}
               for i in range(40)]
    original = json.dumps(records, indent=4)
    compact = json.dumps(records, separators=(",", ":"))
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", len(compact) + 1)
    assert len(original) > agent.OUTPUT_LIMIT
    shown = agent._clip_output(original)
    assert len(shown) <= agent.OUTPUT_LIMIT
    assert json.loads(shown) == records
    assert json.loads(shown)[20]["name"] == "operation_20"


def test_oversize_json_preview_keeps_whole_records_and_exact_omission_count(agent, monkeypatch):
    records = [{"id": i, "description": "detail " * 18} for i in range(40)]
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", 700)
    shown = agent._clip_output(json.dumps(records, indent=2))
    preview = json.loads(shown)
    assert len(shown) <= 700
    assert preview["head"][0] == records[0]
    assert preview["tail"][-1] == records[-1]
    kept = preview["head"] + preview["tail"]
    assert all(record in records for record in kept)
    assert preview["_harness_preview"] == {
        "total_items": 40, "omitted_items": 40 - len(kept)}


def test_json_compaction_preserves_trailing_shell_error(agent, monkeypatch):
    records = [{"id": i, "value": "ok"} for i in range(12)]
    suffix = "\n---\n/bin/sh: Bad substitution\n"
    compact = json.dumps(records, separators=(",", ":"))
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", len(compact + suffix))
    shown = agent._clip_output(json.dumps(records, indent=4) + suffix)
    assert shown == compact + suffix


def test_oversize_mapping_preview_retains_metadata_and_whole_values(agent, monkeypatch):
    original = {"count": 40, "records": [{"id": i} for i in range(40)], "next_page": "opaque"}
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", 180)
    shown = agent._clip_output(json.dumps(original, indent=4))
    preview = json.loads(shown)
    assert len(shown) <= 180
    assert preview["head"] == {"count": 40}
    assert preview["tail"] == {"next_page": "opaque"}
    assert preview["_harness_preview"]["omitted_items"] == 1


def test_full_output_can_be_read_without_repeating_the_original_command(
        agent, monkeypatch, tmp_path):
    records = [{"id": i, "description": "detail " * 18} for i in range(40)]
    original = json.dumps(records, indent=4) + "\n"
    (tmp_path / "fixture.json").write_text(original)
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", 700)
    seen = []

    def complete(messages):
        seen.append(copy.deepcopy(messages))
        if len(seen) == 1:
            return action("printf 'once\\n' >> invocations; cat fixture.json")
        if len(seen) == 2:
            observation = messages[-1]["content"]
            assert "omitted does not mean absent" in observation
            path = observation.split("Full output: ")[1].split(" (use shell tools")[0]
            assert (tmp_path / path).read_text() == original
            return action(shlex.join([sys.executable, "-c",
                "import json,sys; print(json.load(open(sys.argv[1]))[20]['id'])", path]))
        assert messages[-1]["content"] == "exit=0\n20\n"
        return '{"answer":"done"}'

    monkeypatch.setattr(agent, "_complete", complete)
    assert agent.run({"goal": "Inspect synthetic records."}, tmp_path) == "done"
    assert (tmp_path / "invocations").read_text() == "once\n"
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    saved = next(e for e in events if "output_file" in e)
    assert (tmp_path / saved["output_file"]).read_text() == original
    assert next(e["output"] for e in events if "command" in e) == original
    assert json.loads(saved["output_view"])["_harness_preview"]["omitted_items"] > 0


@pytest.mark.parametrize("limit", [1, 30, 201, 4000])
def test_plain_text_views_are_bounded(agent, monkeypatch, limit):
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", limit)
    shown = agent._clip_output("head\n" + "x" * 9000 + "\ntail")
    assert len(shown) <= limit
    if limit >= 201:
        assert shown.startswith("head\n") and shown.endswith("\ntail")


def test_valid_action_after_long_fenced_data_is_not_discarded(agent):
    data = [{"id": i, "example": {"tool": "bash", "command": "touch wrong"}}
            for i in range(100)]
    reply = "```json\n" + json.dumps(data, indent=2) + "\n```\nNext:\n```json\n"
    reply += action("printf correct") + "\n```"
    assert len(reply) > 6161
    assert agent._parse(reply)["command"] == "printf correct"


def test_malformed_complete_block_does_not_hide_later_action(agent):
    assert agent._parse('{broken: "data"}\n' + action("true"))["command"] == "true"


@pytest.mark.parametrize("reply", [
    '{"tool":"bash","command":"printf partial',
    '{"tool":"bash","command":"cat <<EOF\\n{\"command\":\"touch wrong\"}\\n',
    '{"tool":"bash","command":["touch wrong"]}',
    '{"tool":"bash","command":"   "}',
    '[{"tool":"bash","command":"touch wrong"}]',
])
def test_incomplete_or_nested_commands_are_never_actions(agent, reply):
    with pytest.raises(ValueError):
        agent._parse(reply)


def test_recovery_does_not_repeat_long_invalid_reply_and_keeps_last_observation(
        agent, monkeypatch, tmp_path):
    malformed = '{"tool":"bash","command":"' + "printf bad; " * 560
    monkeypatch.setattr(agent, "CONTEXT_CHAR_BUDGET", 2300)
    seen = []
    replies = [action("printf useful-record"), malformed,
               action("printf recovered > recovered.txt"), '{"answer":"done"}']

    def complete(messages):
        seen.append(copy.deepcopy(messages))
        assert sum(len(m["content"]) for m in messages) <= 2300
        if len(seen) == 3:
            assert any("useful-record" in m["content"] for m in messages if m["role"] == "user")
            assert not any("printf bad" in m["content"] for m in messages)
            assert "one short command" in messages[-1]["content"]
        return replies[len(seen) - 1]

    monkeypatch.setattr(agent, "_complete", complete)
    assert agent.run({"goal": "Recover a synthetic action."}, tmp_path) == "done"
    assert (tmp_path / "recovered.txt").read_text() == "recovered"
    trace = (tmp_path / "trace.jsonl").read_text()
    assert malformed in [json.loads(line).get("reply") for line in trace.splitlines()]
    assert "printf bad" not in "\n".join(
        json.loads(line).get("command", "") for line in trace.splitlines())


def test_parse_retry_limit_counts_consecutive_failures(agent, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "PARSE_RETRIES", 1)
    result, _, events = scripted(agent, monkeypatch, tmp_path, [
        "invalid", action("true"), "invalid again", action("true"), '{"answer":"done"}'])
    assert result == "done"
    assert [e["parse_retry"] for e in events if "parse_error" in e] == [0, 0]


def test_trimming_with_nudges_never_orphans_command_results(agent, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CONTEXT_CHAR_BUDGET", 1600)
    monkeypatch.setattr(agent, "FINISH_NUDGE_STEPS", 3)
    result, seen, events = scripted(agent, monkeypatch, tmp_path,
        [action("printf value") for _ in range(7)] + ['{"answer":"done"}'])
    assert result == "done"
    assert any("history_dropped" in e for e in events)
    for messages in seen:
        assert [m["role"] for m in messages[2:]] == ["assistant", "user"] * ((len(messages) - 2) // 2)
        for command, result in zip(messages[2::2], messages[3::2]):
            assert json.loads(command["content"])["command"] == "printf value"
            assert result["content"].startswith("exit=0\nvalue")


def test_non_utf8_command_output_is_decoded_not_fatal(agent, monkeypatch, tmp_path):
    """A command emitting non-UTF-8 bytes must not kill the harness.

    Regression, measured on AgentDojo: `subprocess.run(..., text=True)` decodes
    strictly, so any command whose output contained a non-UTF-8 byte raised
    UnicodeDecodeError inside the harness's own `run()`. Because the exception
    escaped, the task recorded an ordinary `0.0` that was indistinguishable from
    a measured failure. 134 of 601 AgentDojo train tasks (22.3%) died this way --
    the model only had to run `cat` on any binary file. The capture now passes
    errors="replace", so the bytes degrade to U+FFFD instead of aborting the run.
    """
    if not shutil.which("bash"):
        pytest.skip("Bash is not installed")
    # \300 and \377 are never valid UTF-8 start bytes.
    result, seen, events = scripted(
        agent, monkeypatch, tmp_path,
        [action(r"printf '\300\377\376'"), '{"answer":"done"}'])
    executed = next(e for e in events if "command" in e)
    assert result == "done"
    assert executed["exit"] == 0
    assert executed["output"] == "\ufffd\ufffd\ufffd"
    # The harness must still have produced a complete trace.
    assert isinstance(events[-1], dict)
