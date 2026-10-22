"""Submission transport regressions using fabricated payloads and scripted replies.

No dataset imports, release data, verifier gold, model backend, or pilot execution.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def agent(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "submission_recovery_loop", ROOT / "base_harness/loop/agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MAX_STEPS", 5)
    monkeypatch.setattr(module, "FINISH_NUDGE_STEPS", 0)
    return module


@pytest.fixture
def cli(tmp_path):
    # Use the actual public SETUP layout, without any adapter or trusted inputs.
    target = tmp_path / "qa/bin/qa"
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / "data/scaffolds/qa/cli/qa").read_bytes())
    (tmp_path / "qa/prediction.py").write_bytes(
        (ROOT / "data/scaffolds/qa/prediction.py").read_bytes())
    return target


def submit(cli, tmp_path, command, content, route="file"):
    argv = [sys.executable, str(cli), command]
    if route == "file":
        (tmp_path / "source.json").write_text(content)
        argv.append("@source.json")
    elif route == "argument":
        argv.append(content)
    return subprocess.run(argv, input=content if route == "stdin" else None,
                          cwd=tmp_path, capture_output=True, text=True)


def run_replies(agent, monkeypatch, tmp_path, replies):
    seen = []

    def complete(messages):
        seen.append(messages[-1]["content"])
        return replies[len(seen) - 1]

    monkeypatch.setattr(agent, "_complete", complete)
    result = agent.run({"goal": "Submit a synthetic prediction."}, tmp_path)
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    return result, seen, events


@pytest.mark.parametrize("prediction", [
    {"schema_version": 1, "answer": "O'Brien's \"city\"\n北城",
     "supporting_facts": [["Synthetic page", 0]],
     "evidence": [["Subject", "relation", "Object"]]},
    {"schema_version": 1, "answer": "yes", "predicted_evidence": []},
    {"schema_version": 1, "evidence": []},  # Omitted fields stay omitted.
])
def test_object_answer_is_json_and_preserves_every_submitted_field(
        agent, monkeypatch, tmp_path, prediction):
    result, _, events = run_replies(agent, monkeypatch, tmp_path,
                                    [json.dumps({"answer": prediction})])
    assert json.loads(result) == prediction
    assert json.loads((tmp_path / "answer.txt").read_text()) == prediction
    assert len([event for event in events if event.get("submitted")]) == 1


MALFORMED = [
    "{'schema_version': 1, 'answer': 'synthetic'}",  # Python repr, not JSON.
    '{"schema_version":1,"answer":"x","evidence":[]',  # Missing closing brace.
    '{"schema_version":1,"answer":"cut',  # Truncated string.
    '{"schema_version":1,"answer":}',  # Missing value.
    '{"schema_version":1,"answer":"x",}',  # Trailing comma.
    '{"schema_version":1,"answer":"x"} trailing',
]


@pytest.mark.parametrize("broken", MALFORMED)
def test_inner_json_failure_uses_parse_retry_without_guessing(
        agent, monkeypatch, tmp_path, broken):
    recovered = {"schema_version": 1, "answer": "replacement"}
    result, seen, events = run_replies(agent, monkeypatch, tmp_path, [
        json.dumps({"answer": broken}), json.dumps({"answer": recovered})])
    assert json.loads(result) == recovered
    assert "malformed JSON" in seen[1]
    assert 'complete JSON object in "answer"' in seen[1]
    assert [event["parse_retry"] for event in events if "parse_error" in event] == [0]
    assert not any("command" in event for event in events)
    assert len([event for event in events if event.get("submitted")]) == 1


def test_repeated_truncation_is_bounded_and_never_auto_completed(
        agent, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "PARSE_RETRIES", 1)
    broken = json.dumps({"answer": '{"schema_version":1,"answer":"x"'})
    result, seen, events = run_replies(agent, monkeypatch, tmp_path, [broken, broken])
    assert result == "" and len(seen) == 2
    assert not any(event.get("submitted") for event in events)
    assert [event["parse_retry"] for event in events if "parse_error" in event] == [0, 1]


@pytest.mark.parametrize("value", [None, 1, True, ["x"]])
def test_non_answer_types_are_retried_instead_of_stringified(agent, value):
    with pytest.raises(ValueError, match="string or a JSON object"):
        agent._parse(json.dumps({"answer": value}))


@pytest.mark.parametrize("content", [
    "legacy answer with 'quotes'", "",
    '  {"schema_version":1,"answer":"x","supporting_facts":[],"evidence":[]}\n',
])
def test_valid_legacy_and_json_strings_keep_their_bytes(
        agent, monkeypatch, tmp_path, content):
    result, _, _ = run_replies(agent, monkeypatch, tmp_path, [json.dumps({"answer": content})])
    assert result == content
    assert (tmp_path / "answer.txt").read_text() == content


@pytest.mark.parametrize("route", ["file", "argument", "stdin"])
@pytest.mark.parametrize("broken", MALFORMED)
def test_cli_rejects_bad_json_and_preserves_prior_submission(cli, tmp_path, route, broken):
    answer = tmp_path / "answer.txt"
    answer.write_text("previous submission\n")
    proc = submit(cli, tmp_path, "submit", broken, route)
    assert proc.returncode == 2
    assert "invalid submission JSON" in proc.stderr
    assert answer.read_text() == "previous submission\n"
    answer.unlink()
    assert submit(cli, tmp_path, "submit", broken, route).returncode == 2
    assert not answer.exists()


@pytest.mark.parametrize("route", ["file", "argument", "stdin"])
@pytest.mark.parametrize("content", [
    "plain legacy answer", "'quoted legacy answer'",
    '{"schema_version":1,"answer":"x","supporting_facts":[],"evidence":[]}',
    '{"schema_version":1,"answer":"x","predicted_evidence":[["Page",0]]}',
    '{"schema_version":1,"evidence":[]}',
])
def test_cli_submit_transports_legacy_and_adapter_specific_json(cli, tmp_path, route, content):
    proc = submit(cli, tmp_path, "submit", content, route)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "answer.txt").read_text() == content + "\n"
    assert not (tmp_path / "prediction.json").exists()


@pytest.mark.parametrize("content", [
    *MALFORMED,
    '{"schema_version":1,"predicted_evidence":[]}',
    '{"schema_version":1,"answer":"x","gold":"untrusted"}',
    '{"schema_version":1,"answer":"x","predicted_evidence":[["Page",true]]}',
    '{"schema_version":1,"answer":"x","evidence":[["S","R","O"]]}',
])
def test_submit_json_retains_shared_schema_validation(cli, tmp_path, content):
    previous = '{"schema_version":1,"answer":"previous"}\n'
    (tmp_path / "prediction.json").write_text(previous)
    proc = submit(cli, tmp_path, "submit-json", content)
    assert proc.returncode == 2
    assert (tmp_path / "prediction.json").read_text() == previous
    assert not (tmp_path / "answer.txt").exists()


def test_submit_json_accepts_valid_shared_prediction(cli, tmp_path):
    prediction = {"schema_version": 1, "answer": "synthetic", "predicted_evidence": [["P", 0]]}
    proc = submit(cli, tmp_path, "submit-json", json.dumps(prediction))
    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path / "prediction.json").read_text()) == prediction
    assert not (tmp_path / "answer.txt").exists()
