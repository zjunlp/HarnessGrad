"""Shared prediction.json routing on fabricated 2Wiki gold, without a dataset run."""
import importlib
import json
from unittest.mock import patch

import pytest

from data.scaffolds.qa import base
from data.scaffolds.qa.verify import check

with patch.object(base.QASpec, "records", return_value=[]):
    dataset = importlib.import_module("data.datasets.twowikimultihopqa")


@pytest.fixture
def gold():
    return {"id": "adaptfid1-fabricated", "type": "synthetic", "answer": "fictional blue",
            "aliases": [], "supporting_facts": [["Fabricated Page", 0]],
            "evidences": [["Invented Subject", "invented relation", "Invented Object"]],
            "evidences_id": [], "evidence_aliases": {}}


def run_check(tmp_path, gold, *, structured=None, answer=None):
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold))
    if structured is not None:
        (tmp_path / "prediction.json").write_text(json.dumps(structured))
    if answer is not None:
        (tmp_path / "answer.txt").write_text(answer)
    reward_path = tmp_path / "reward.txt"
    evidence_path = tmp_path / "evidence.json"
    code = check.main([
        "--dataset", "twowikimultihopqa", "--gold", str(gold_path),
        "--prediction", str(tmp_path / "prediction.json"),
        "--answer", str(tmp_path / "answer.txt"),
        "--reward-file", str(reward_path), "--evidence-file", str(evidence_path),
    ])
    return code, reward_path, json.loads(evidence_path.read_text())


@pytest.mark.parametrize("pairs", [[], [["Fabricated Page", 0]], [["Wrong Page", 9]]])
def test_shared_pair_evidence_is_refused_by_name_instead_of_silently_dropped(
        tmp_path, gold, pairs):
    pred = {"schema_version": 1, "answer": gold["answer"], "predicted_evidence": pairs}
    code, reward, evidence = run_check(
        tmp_path, gold, structured=pred, answer=gold["answer"])
    assert code == 3
    assert not reward.exists()
    assert evidence["measured"] is False
    assert "twowiki_prediction_json_pair_schema_unsupported" in evidence["error"]


def test_shared_structured_answer_without_evidence_uses_joint_metrics(tmp_path, gold):
    code, reward, evidence = run_check(tmp_path, gold, structured={
        "schema_version": 1, "answer": gold["answer"]})
    assert code == 1
    assert float(reward.read_text()) == 0.0
    assert evidence["measured"] is True
    assert evidence["official"]["mode"] == "structured"
    assert evidence["official"]["f1"] == 1.0
    assert evidence["official"]["joint_f1"] == 0.0


@pytest.mark.parametrize("channel", ["legacy", "full", "partial"])
def test_answer_txt_keeps_legacy_and_structured_scores(tmp_path, gold, channel):
    full = {"schema_version": 1, "answer": gold["answer"],
            "supporting_facts": gold["supporting_facts"], "evidence": gold["evidences"]}
    answer = (gold["answer"] if channel == "legacy" else json.dumps(
        full if channel == "full" else {"schema_version": 1, "answer": gold["answer"]}))
    code, reward, evidence = run_check(tmp_path, gold, answer=answer)
    expected = 0.0 if channel == "partial" else 1.0
    assert code == (0 if expected else 1)
    assert float(reward.read_text()) == expected
    assert evidence["official"]["mode"] == (
        "legacy_answer_only" if channel == "legacy" else "structured")
    if channel != "legacy":
        assert evidence["official"]["joint_f1"] == expected
