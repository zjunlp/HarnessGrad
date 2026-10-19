"""Pure judge controls and pinned official prompt/parser/arithmetic comparisons.

Set MAB_UPSTREAM to the existing pinned source to enable differential tests.
No provider SDK is imported and no API calls are made by these tests.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
from types import SimpleNamespace

import pytest

from data.datasets.memoryagentbench.lib import judge


def summary(replies, *, keypoints=None, prediction="  candidate summary  ", model=None):
    responses = iter(replies)
    messages = []

    def complete(value):
        messages.append(value)
        return next(responses)

    result = judge.score_question(judge.SUMMARY, prediction, ["Full expert summary."],
        {"keypoints": ["first fact", "second fact"] if keypoints is None else keypoints},
        complete, judge.profile(judge.SUMMARY, model))
    return result, messages


def qa(reply, *, kind="multi-session", question_id="example", model=None):
    messages = []

    def complete(value):
        messages.append(value)
        return reply

    result = judge.score_question(judge.LONGMEM, "candidate", ["correct", "alternative"],
        {"question": "Original question?", "question_type": kind, "question_id": question_id},
        complete, judge.profile(judge.LONGMEM, model))
    return result, messages


def test_profile_preserves_official_sampling_and_distinguishes_override():
    summ, longmem = judge.profile(judge.SUMMARY), judge.profile(judge.LONGMEM)
    assert (summ["model"], summ["max_output_tokens"], summ["calls_per_question"]) == ("gpt-4o-2024-05-13", 4096, 3)
    assert summ["parameters"] == {"temperature": 0.1, "top_p": 0.9, "seed": 42}
    assert (longmem["model"], longmem["max_output_tokens"], longmem["calls_per_question"]) == ("gpt-4o", 10, 1)
    assert longmem["parameters"] == {"temperature": 0}
    assert summ["omitted_api_defaults"] == longmem["omitted_api_defaults"] == {"n": 1, "stop": None}
    other = judge.profile(judge.LONGMEM, "explicit-other-model")
    assert other["model_override"] is True and other["identity"] != longmem["identity"]
    assert judge.profile(judge.LONGMEM, "gpt-4o") == longmem
    assert "require_yes_or_no_word" in longmem["admission"]
    assert json.loads(json.dumps(other)) == other


def test_summary_formula_and_private_evidence():
    result, messages = summary(['{"fluency":1}', 'reason {"recall":1}', '{"precision":1,"sentence_count":4}'])
    assert result["status"] == "measured" and result["metric"] == "gpt-4-f1"
    assert result["score"] == pytest.approx(1/3)
    assert [item["kind"] for item in result["judge"]["calls"]] == ["fluency", "recall", "precision"]
    assert all(len(item["messages_sha256"]) == 64 for item in result["judge"]["calls"])
    assert "Full expert summary." in messages[2][0]["content"]
    assert '"candidate summary"' in messages[0][0]["content"]


def test_empty_submitted_summary_still_runs_official_three_call_protocol():
    result, messages = summary(['{"fluency":0}', '{"recall":0}',
                                '{"precision":0,"sentence_count":0}'], prediction="")
    assert result["status"] == "measured" and result["score"] == 0
    assert len(messages) == 3 and 'Text: ""' in messages[0][0]["content"]


@pytest.mark.parametrize("reply,expected", [("yes", 1), (" YES. ", 1), ("No", 0), ("No, yesterday.", 1), ("no, yes", 1)])
def test_qa_retains_official_yes_substring_after_admission(reply, expected):
    result, messages = qa(reply)
    assert result["score"] == expected and result["metric"] == "Accuracy"
    assert "Correct Answer: ['correct', 'alternative']" in messages[0][0]["content"]


@pytest.mark.parametrize("reply", [None, "", "  ", "perhaps", "yesterday", "YESNO", 1])
def test_qa_rejects_missing_or_nondecision_outputs(reply):
    with pytest.raises(judge.JudgeError):
        qa(reply)


@pytest.mark.parametrize("replies", [
    [None, '{}', '{}'], ['{}', '{}', '{}'], ['not-json', '{}', '{}'],
    ['{"fluency":true}', '{"recall":1}', '{"precision":1,"sentence_count":1}'],
    ['{"fluency":2}', '{"recall":1}', '{"precision":1,"sentence_count":1}'],
    ['{"fluency":1}', '{"recall":3}', '{"precision":1,"sentence_count":1}'],
    ['{"fluency":1}', '{"recall":-1}', '{"precision":1,"sentence_count":1}'],
    ['{"fluency":1}', '{"recall":1.5}', '{"precision":1,"sentence_count":1}'],
    ['{"fluency":1}', '{"recall":1}', '{"precision":2,"sentence_count":1}'],
    ['{"fluency":1}', '{"recall":1}', '{"precision":1,"sentence_count":0}'],
    ['{"fluency":1}', '{"recall":1}', '{"precision":NaN,"sentence_count":1}'],
])
def test_summary_errors_withhold_score(replies):
    with pytest.raises(judge.JudgeError):
        summary(replies)


def test_profile_and_input_validation_precedes_transport(monkeypatch):
    def forbidden(_):
        pytest.fail("invalid input reached judge transport")

    profile = judge.profile(judge.SUMMARY)
    tampered = copy.deepcopy(profile)
    tampered["parameters"]["temperature"] = 1
    for value in [tampered, {}, None]:
        with pytest.raises(judge.JudgeError):
            judge.score_question(judge.SUMMARY, "text", ["gold"], {"keypoints": []}, forbidden, value)
    for answers in [[], "gold", [None], [["nested"]]]:
        with pytest.raises(judge.JudgeError):
            judge.score_question(judge.SUMMARY, "text", answers, {"keypoints": []}, forbidden, profile)
    with pytest.raises(judge.JudgeError):
        judge.score_question(judge.SUMMARY, "text", ["gold"], {"keypoints": "bad"}, forbidden, profile)
    monkeypatch.setattr(judge, "PROMPTS_SHA256", "invalid")
    with pytest.raises(judge.JudgeError, match="fixed source"):
        judge.profile(judge.SUMMARY)


def test_transport_failure_is_not_zero_or_retried():
    calls = []

    def fail(messages):
        calls.append(messages)
        raise TimeoutError("synthetic provider timeout")

    with pytest.raises(TimeoutError):
        judge.score_question(judge.LONGMEM, "candidate", ["gold"],
            {"question": "question", "question_type": "multi-session", "question_id": "id"},
            fail, judge.profile(judge.LONGMEM))
    assert len(calls) == 1


@pytest.fixture(scope="module")
def official():
    checkout = os.environ.get("MAB_UPSTREAM")
    if not checkout:
        pytest.skip("MAB_UPSTREAM is unset; pinned official source required")
    resources = judge._prompts()
    namespace = {"json": json, "re": re, "np": SimpleNamespace(mean=statistics.mean),
                 "tqdm": lambda values: values}
    for filename, digest in resources["source_sha256"].items():
        path = Path(checkout) / "llm_based_eval" / filename
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        tree = ast.parse(path.read_text())
        selected = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in ("parse_json", "check_metrics", "get_anscheck_prompt"):
                selected.append(node)
            elif (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id.endswith("_prompt_book")):
                selected.append(node)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("kind", ["single-session-user", "single-session-assistant", "multi-session",
    "temporal-reasoning", "knowledge-update", "single-session-preference"])
@pytest.mark.parametrize("abstention", [False, True])
def test_qa_prompt_exact_official(official, kind, abstention):
    _, messages = qa("yes", kind=kind, question_id="example_abs" if abstention else "example")
    expected = official["get_anscheck_prompt"](kind, "Original question?", ["correct", "alternative"],
                                              "candidate", abstention=abstention)
    assert messages == [[{"role": "user", "content": expected}]]


@pytest.mark.parametrize("text", ["reason {\"recall\": 1}", '{"recall":2} then {"recall":1}',
    '```json\n{"outer":{"recall":1}}\n```', 'malformed {bad}', 'no object', '[]'])
def test_json_parser_matches_official(official, text):
    assert judge._parse_json(text) == official["parse_json"](text)


@pytest.mark.parametrize("fluency,found,total,supported,keypoints", [
    (1, 1, 4, 1, ["first fact", "second fact"]),
    (0, 2, 2, 2, ["first fact", "second fact"]),
    (1, 0, 0, 0, []), (1, 2, 2, 2, ["first fact", "second fact"]),
])
def test_summary_exact_official_prompts_and_formula(official, tmp_path, fluency, found, total, supported, keypoints):
    replies = [json.dumps({"fluency": fluency}), json.dumps({"recall": found}),
               json.dumps({"precision": supported, "sentence_count": total})]
    result, messages = summary(replies, keypoints=keypoints)
    requested = []

    class Model:
        def generate_batch(self, *, prompt, batch_file):
            requested.extend(prompt)
            return [{"output": value} for value in replies]

    namespace = dict(official)
    namespace["load_data_from_huggingface"] = lambda *_: {"doc": {"keypoints": keypoints}}
    # Rebind only the official function's global dependency loader; execute its actual body.
    import types
    run = types.FunctionType(official["check_metrics"].__code__, namespace)
    source = tmp_path / "infbench-output.json"
    source.write_text(json.dumps({"data": [{"qa_pair_id": "doc", "answer": ["Full expert summary."],
                                          "output": "  candidate summary  "}], "averaged_metrics": {}}))
    expected = run(Model(), str(source), str(tmp_path / "judged.json"), SimpleNamespace(huggingface_dataset_name="fixed"))
    assert requested == [value[0]["content"] for value in messages]
    assert result["score"] == expected["averaged_metrics"]["gpt-4-f1"]
    for name, value in result["judge"]["details"].items():
        assert value == expected["data"][0]["gpt-4-scores"][name]
