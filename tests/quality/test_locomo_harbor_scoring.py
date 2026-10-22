"""Pinned Harbor differential: full scoring entry point, input rendering, metric.

Default tests are synthetic and offline. Optional full released-data comparisons
run only with LOCOMO_AUDIT_DATA pointing at the SHA-256-pinned locomo10.json.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/locomo_harbor"
LIB = ROOT / "data/datasets/locomo/lib"
SPEC = ROOT / "data/datasets/locomo/specs/source.json"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


inputs = _module("locomo_inputs", LIB / "inputs.py")
scoring = _module("locomo_scoring", LIB / "scoring.py")
reference_inputs = _module("harbor_locomo_inputs", FIXTURE / "adapter.py")
reference_scorer = _module("harbor_locomo_verifier", FIXTURE / "task-template/tests/verifier.py")
reference_metric = _module("harbor_locomo_metric", FIXTURE / "metric.py")


@pytest.fixture
def row():
    return {
        "sample_id": "synthetic_17",
        "conversation": {
            "speaker_a": "Alice", "speaker_b": "Bob",
            "session_10_date_time": "11 May 2023",
            "session_10": [{"speaker": "Bob", "text": "  Hello!  ", "blip_caption": "a lake"}],
            "session_2_date_time": "7 May 2023",
            "session_2": [{"speaker": "Alice", "text": "I visited Paris and Rome."}, {"text": None}],
        },
        "qa": [
            {"question": "Which cities?", "category": 1, "answer": "Paris, Rome", "evidence": ["D2:1"]},
            {"question": "When?", "category": 2, "answer": "7 May 2023"},
            {"question": "Which field?", "category": 3, "answer": "psychology; counseling"},
            {"question": "Which city?", "category": 4, "answer": "Paris"},
            {"question": "Her middle name?", "category": 5, "adversarial_answer": "Margaret"},
        ],
    }


def _reference_score(tmp_path, text, gold):
    reference_scorer.GROUND_TRUTH_PATH = tmp_path / "gold.json"
    reference_scorer.ANSWERS_PATH = tmp_path / "answers.json"
    reference_scorer.REWARD_PATH = tmp_path / "reward.json"
    reference_scorer.DETAILS_PATH = tmp_path / "details.json"
    reference_scorer.GROUND_TRUTH_PATH.write_text(json.dumps({"questions": gold}))
    if text is None:
        reference_scorer.ANSWERS_PATH.unlink(missing_ok=True)
    else:
        reference_scorer.ANSWERS_PATH.write_text(text)
    reference_scorer.main()
    return {
        **json.loads(reference_scorer.REWARD_PATH.read_text()),
        **json.loads(reference_scorer.DETAILS_PATH.read_text()),
    }


def test_source_pin():
    source = json.loads(SPEC.read_text())
    assert source["harbor"]["commit"] == "3e9f14a7c1033896cc600d27de69d6948bcb3410"
    assert source["data"]["commit"] == "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
    assert source["data"]["sha256"] == "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
    assert source["dependency"] == {"nltk": "3.10.3", "nltk_corpora_required": False}


@pytest.mark.parametrize("relative", ["adapter.py", "metric.py", "task-template/tests/verifier.py", "task-template/instruction.md", "LICENSE", "parity_experiment.json"])
def test_reference_bytes(relative):
    metadata = json.loads(SPEC.read_text())["harbor"]["files"][relative]
    body = (FIXTURE / relative).read_bytes()
    assert hashlib.sha256(body).hexdigest() == metadata["sha256"]
    if "git_blob" in metadata:
        assert hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest() == metadata["git_blob"]


def test_input_and_private_gold_match_harbor(row):
    expected_gold = reference_inputs._ground_truth(row["sample_id"], row["qa"])
    assert inputs.gold_questions(row) == expected_gold["questions"]
    assert inputs.oracle_answers(row) == reference_inputs._oracle_answers(row["sample_id"], row["qa"])
    public = inputs.render_questions(row)
    assert public == [{"index": q["index"], "question": q["question"]} for q in expected_gold["questions"]]
    assert all(set(q) == {"index", "question"} for q in public)
    conversation = row["conversation"]
    expected_conversation = reference_inputs.CONV_START_PROMPT.format(
        speaker_a=conversation["speaker_a"], speaker_b=conversation["speaker_b"],
    ) + "\n\n" + reference_inputs._format_conversation(conversation)
    assert inputs.render_conversation(row) == expected_conversation
    if row["sample_id"] == "synthetic_17":
        assert expected_conversation.index("7 May") < expected_conversation.index("11 May")
    instruction = (FIXTURE / "task-template/instruction.md").read_text().replace(
        "{questions}", reference_inputs._agent_question_list(expected_gold),
    )
    assert inputs.render_instruction(row) == instruction.replace("/app/conversation.md", "conversation.md").replace("/workspace/answers.json", "answers.json")


@pytest.mark.parametrize("text", [
    None, "", "{", "null", "[]", "42", '"text"', "{}",
    '{"0": "Paris"}', '{"4": "(a)"}', '{"4": "(b)"}', '{"4": "c"}',
    '{"4": "xyz"}', '{"4": "No information available"}', '{"4": "not mentioned"}',
    '{"4": "unknown"}', '{"4": ""}', '{"3": "(b) Paris"}',
    '{"0": "the PARIS and Rome", "1": "May 7, 2023", "2": "psychologist"}',
    '{"0": ["Paris", "Rome"], "1": 2023, "2": null, "3": false, "4": {"a": 1}}',
    '{"3": "Rome", "3": "Paris", "999": "ignored"}',
    '{"3": "\\u00e9 Paris"}',
])
def test_full_reference_scorer_differential(tmp_path, row, text):
    gold = inputs.gold_questions(row)
    assert scoring.score_answers(text, gold) == _reference_score(tmp_path, text, gold)


def test_oracle_and_nop_controls(tmp_path, row):
    gold = inputs.gold_questions(row)
    oracle = json.dumps(inputs.oracle_answers(row))
    assert scoring.score_answers(oracle, gold) == _reference_score(tmp_path, oracle, gold)
    assert scoring.score_answers(oracle, gold)["reward"] == 1.0
    assert scoring.score_answers(None, gold)["reward"] == 0.0
    assert scoring.score_answers(None, gold)["num_questions"] == 5


def test_full_precision_and_prefix_handling_are_not_official_gpt_cleanup(row):
    gold = inputs.gold_questions(row)[3:4]
    scored = scoring.score_answers('{"3": "(b) Paris"}', gold)
    assert scored["reward"] == 2 / 3
    assert scored["reward"] != round(scored["reward"], 3)


@pytest.mark.parametrize("prediction", ["a", "b", "(a)", "(b)", "z", "xyz", "not mentioned", "no information available", "I don't know", ""])
def test_both_deterministic_cat5_permutations(tmp_path, row, prediction):
    letters = set()
    for index in range(20):
        row["sample_id"] = f"seed{index}"
        gold = inputs.gold_questions(row)[4:5]
        letters.add(gold[0]["options"]["refusal_letter"])
        text = json.dumps({"4": prediction})
        assert scoring.score_answers(text, gold) == _reference_score(tmp_path, text, gold)
    assert letters == {"a", "b"}


def test_empty_gold_and_nullable_answer(tmp_path):
    for gold in ([], [{"index": 0, "category": 4, "answer": None}], [{"index": 0, "category": 2, "answer": 2023}]):
        assert scoring.score_answers('{"0": 2023}', gold) == _reference_score(tmp_path, '{"0": 2023}', gold)


@pytest.mark.parametrize("scores", [
    [], [None], [None, None], [{"score_sum": 2, "num_questions": 3}],
    [{"score_sum": 1, "num_questions": 1}, {"score_sum": 0, "num_questions": 9}],
    [{"score_sum": 1, "num_questions": 1}, None],
    [{"score_sum": 0, "num_questions": 0}],
])
def test_aggregate_matches_reference(tmp_path, scores):
    source = tmp_path / "rewards.jsonl"
    target = tmp_path / "aggregate.json"
    source.write_text("".join(json.dumps(score) + "\n" for score in scores))
    reference_metric.main(source, target)
    assert scoring.aggregate_scores(scores) == json.loads(target.read_text())


def test_question_weighting_and_unmeasured_retention():
    scores = [{"score_sum": 1, "num_questions": 1}, {"score_sum": 0, "num_questions": 9}]
    assert scoring.aggregate_scores(scores)["micro_avg_reward"] == 0.1
    failed = scoring.aggregate_scores(scores + [None])
    assert "micro_avg_reward" not in failed
    assert failed == {"num_failed_trials": 1, "num_questions_scored": 10}


@pytest.mark.skipif(not os.environ.get("LOCOMO_AUDIT_DATA"), reason="optional SHA-pinned released-data audit")
def test_released_dataset_differential(tmp_path):
    raw = Path(os.environ["LOCOMO_AUDIT_DATA"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == json.loads(SPEC.read_text())["data"]["sha256"]
    rows = json.loads(raw)
    counts = {}
    assert len(rows) == 10
    for row in rows:
        test_input_and_private_gold_match_harbor(row)
        gold = inputs.gold_questions(row)
        for q in gold:
            counts[q["category"]] = counts.get(q["category"], 0) + 1
        oracle = inputs.oracle_answers(row)
        variants = [None, json.dumps(oracle), json.dumps({k: v for k, v in oracle.items() if int(k) % 2}), json.dumps({k: "(b) " + v for k, v in oracle.items()})]
        for text in variants:
            assert scoring.score_answers(text, gold) == _reference_score(tmp_path, text, gold)
    assert counts == {1: 282, 2: 321, 3: 96, 4: 841, 5: 446}
