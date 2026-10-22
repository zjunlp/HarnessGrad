"""MuSiQue differential and verifier tests: fabricated train data only, no model."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import data.datasets.musique as dataset
from data.scaffolds.qa import base

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/musique_official"
HASHES = {
    "answer.py": "10368f619b4d5ef5d83748c05a96c0afd332a14ab5c010740c98d58dfaefe974",
    "support.py": "ac16c0daf458a6a4d6db97682c2340fe5b5936a947bf32c04dc3bf16406077c6",
    "metric.py": "c858d1bfda2f0b005065e87a402cd2f82154eb7eed5916845ae130759cc3a299",
}


@pytest.fixture(autouse=True)
def forbid_released_payloads(monkeypatch):
    def forbidden():
        pytest.fail("synthetic tests must not load released MuSiQue payloads")

    monkeypatch.setattr(dataset._SPEC, "_rows", forbidden)
    monkeypatch.setattr(dataset, "_rows", forbidden)


@pytest.fixture
def upstream(monkeypatch):
    # Load the real pinned classes with their real imports, restoring sys.modules
    # afterward so the fixture does not affect any other scorer's tests.
    package = types.ModuleType("metrics")
    package.__path__ = [str(FIXTURES / "metrics")]
    monkeypatch.setitem(sys.modules, "metrics", package)
    modules = {}
    for filename, digest in HASHES.items():
        path = FIXTURES / "metrics" / filename
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        name = f"metrics.{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[path.stem] = module
    return modules


@pytest.fixture
def train_record():
    # Deliberately noncontiguous/reordered idx, duplicate title, and annotations
    # unrelated to the text. These are invented, never read from the benchmark.
    row = {
        "id": "fabricated-train-0", "question": "Which invented color pair?",
        "answerable": True, "answer": "SYNTHETIC_GOLD red blue",
        "answer_aliases": ["ALIAS_SECRET azure crimson"],
        "question_decomposition": [{"answer": "DECOMPOSITION_SECRET"}],
        "paragraphs": [
            {"idx": 7, "title": "Shared title", "paragraph_text": "Invented first context.",
             "is_supporting": True},
            {"idx": 2, "title": "Distractor", "paragraph_text": "Invented second context.",
             "is_supporting": False},
            {"idx": 11, "title": "Shared title", "paragraph_text": "Invented third context.",
             "is_supporting": True},
        ],
    }
    return {"task_id": "musique/train/0", "split": "train", "index": 0,
            "row": row, "question": dataset._question(row), "gold": dataset._gold(row)}


def prediction(answer, indices):
    return {"schema_version": 1, "answer": answer,
            "predicted_evidence": [["Descriptive title", index] for index in indices]}


@pytest.mark.parametrize("answer", [
    "SYNTHETIC_GOLD red blue", "ALIAS_SECRET azure crimson", "red", "a blue, red",
    "wrong", "red red", "The City", "MÜNCHEN", "don't", "a",
])
@pytest.mark.parametrize("indices", [[], [7], [11, 7], [7, 7, 11], [2],
                                         [7, 11, 2], [7, 999], [0, 1]])
def test_official_components_differential(upstream, train_record, answer, indices):
    gold = train_record["gold"]
    answer_metric = upstream["answer"].AnswerMetric()
    support_metric = upstream["support"].SupportMetric()
    answer_metric(answer, [gold["answer"], *gold["aliases"]])
    support_metric(indices, [7, 11])
    answer_em, answer_f1 = answer_metric.get_metric()
    support_em, support_f1 = support_metric.get_metric()
    result = dataset.score_prediction(prediction(answer, indices), gold)
    official = result["official"]
    assert official["answer_f1"] == answer_f1
    assert official["answer_em"] == answer_em
    assert official["support_f1"] == support_f1
    assert official["support_em"] == support_em
    assert result["reward"] == answer_f1 * support_f1
    assert result["correct"] == bool(answer_em * support_em)
    assert "not an official metric" in official["reward_policy"]


@pytest.mark.parametrize("answer,gold", [("", ""), ("", "a"), ("foo foo", "foo"),
                                          ("abc-def", "abcdef"), ("", "right")])
def test_answer_empty_and_normalization_differential(upstream, answer, gold):
    assert dataset.compute_f1(gold, answer) == upstream["answer"].compute_f1(gold, answer)
    assert dataset.compute_exact(gold, answer) == upstream["answer"].compute_exact(gold, answer)


def test_empty_support_corner_case(upstream):
    metric = upstream["support"].SupportMetric()
    metric([], [])
    assert metric.get_metric() == (1.0, 1.0)
    assert dataset.compute_support([], []) == {"f1": 1.0, "em": 1}


@pytest.mark.parametrize("evidence", [None, []])
def test_missing_evidence_is_zero_and_never_legacy(train_record, evidence):
    gold = train_record["gold"]
    structured = {"schema_version": 1, "answer": gold["answer"],
                  "predicted_evidence": evidence}
    for result in [dataset.score_prediction(structured, gold),
                   dataset.score_prediction({"schema_version": 1, "answer": gold["answer"]}, gold),
                   dataset.score(gold["answer"], gold)]:
        assert result["reward"] == 0.0
        assert result["official"]["answer_f1"] == 1.0
        assert result["official"]["support_f1"] == 0.0
        assert result["official"]["mode"] == "answer_support"


def test_explicit_legacy_preserves_old_scores(train_record):
    gold = train_record["gold"]
    for answer, expected in [(gold["aliases"][0], 1.0), ("red", 0.5), ("", 0.0)]:
        result = dataset.score(answer, gold, mode="legacy_answer_only")
        assert result["reward"] == expected
        assert result["official"]["support_f1"] is None
        assert "legacy" in result["official"]["metric"]
        assert "joint_f1" not in result["official"]
    with pytest.raises(ValueError, match="mode"):
        dataset.score("red", gold, mode="answer_only")
    for key in ("mode", "gold", "predicted_support_idxs"):
        with pytest.raises(ValueError, match="unsupported"):
            dataset.score_prediction({**prediction("red", [7]), key: "legacy_answer_only"}, gold)


def test_missing_trusted_support_is_unmeasured_not_legacy(train_record):
    gold = {key: value for key, value in train_record["gold"].items() if key != "supporting_facts"}
    with pytest.raises(ValueError, match="trusted gold"):
        dataset.score(gold["answer"], gold)
    assert dataset.score(gold["answer"], gold, mode="legacy_answer_only")["reward"] == 1.0


def test_macro_aggregate_matches_upstream_and_mean_of_products(upstream, train_record):
    gold = train_record["gold"]
    answer_metric, support_metric = upstream["answer"].AnswerMetric(), upstream["support"].SupportMetric()
    results = []
    # Anti-correlated components distinguish mean(products) from product(means).
    for answer, indices in [(gold["answer"], []), ("wrong", [7, 11]), ("red", [7])]:
        answer_metric(answer, [gold["answer"], *gold["aliases"]])
        support_metric(indices, [7, 11])
        results.append(dataset.score_prediction(prediction(answer, indices), gold))
    summary = dataset.aggregate(results)
    answer_em, answer_f1 = answer_metric.get_metric()
    _, support_f1 = support_metric.get_metric()
    assert summary["answer_f1"] == round(answer_f1, 3)
    assert summary["answer_em"] == round(answer_em, 3)
    assert summary["support_f1"] == round(support_f1, 3)
    assert summary["reward"] == pytest.approx(1 / 9)
    assert summary["reward"] != pytest.approx(answer_f1 * support_f1)
    legacy = dataset.score("red", gold, mode="legacy_answer_only")
    assert dataset.aggregate([legacy])["support_f1"] is None
    with pytest.raises(ValueError, match="mix"):
        dataset.aggregate([*results, legacy])
    with pytest.raises(ValueError, match="empty"):
        dataset.aggregate([])


def test_public_context_goal_and_setup_do_not_leak_gold(train_record):
    context, gold = train_record["question"], train_record["gold"]
    goal = dataset._goal(context, gold)
    changed_gold = {"anything": "ANOTHER_SECRET"}
    assert goal == dataset._goal(context, changed_gold)
    assert context.index("[7]") < context.index("[2]") < context.index("[11]")
    setup = dataset._SPEC.setup(train_record)
    public = json.dumps({"goal": goal, "setup": setup})
    for secret in [gold["answer"], *gold["aliases"], "DECOMPOSITION_SECRET",
                   "is_supporting", "question_decomposition", "supporting_facts"]:
        assert secret not in public
    assert "predicted_evidence" in goal
    assert "platform" in goal
    assert "LEGACY ANSWER-ONLY" in dataset._goal(context, gold, mode="legacy_answer_only")
    inputs = dataset.verify_for(train_record)["inputs"]
    trusted = next(entry["content"] for entry in inputs if entry["path"] == "gold.json")
    assert json.loads(trusted)["supporting_facts"] == [["Shared title", 7], ["Shared title", 11]]


def test_load_mode_goals_and_verifier_stay_aligned_on_synthetic_records(monkeypatch, train_record):
    records = [{**train_record, "task_id": f"musique/train/{index}", "index": index}
               for index in range(12)]
    monkeypatch.setattr(dataset._SPEC, "_records", records)
    monkeypatch.setattr(dataset._SPEC, "mode", dataset.DEFAULT_MODE)
    for mode in (dataset.LEGACY_MODE, dataset.DEFAULT_MODE):
        tasks, scorable = dataset.load(mode=mode)
        assert len(tasks) == len(scorable) == 12
        # No declared division: MuSiQue's dev file is one scored split, so the whole
        # pool is evaluation material (2026-10-24; the hash third is gone).
        assert dataset.SPLIT is None and dataset.OFFICIAL_EVAL is None
        assert {task["task_id"] for task in tasks} == set(scorable)
        assert set(dataset.ENV) == set(scorable)
        for task in tasks:
            assert ("LEGACY ANSWER-ONLY" in task["goal"]) == (mode == dataset.LEGACY_MODE)
            verifier = dataset.VERIFY[task["task_id"]]
            assert verifier["argv"][verifier["argv"].index("--score-mode") + 1] == mode
            assert "--redact-gold" in verifier["argv"]
    with pytest.raises(ValueError, match="mode"):
        dataset.load(mode="unknown")
    from data.scaffolds.qa.control import _oracle_prediction
    assert _oracle_prediction(dataset, train_record) == {
        "schema_version": 1, "answer": train_record["gold"]["answer"],
        "predicted_evidence": [["Shared title", 7], ["Shared title", 11]],
    }


def materialize(entries, work):
    for entry in entries:
        target = work / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"], encoding="utf-8")
        else:
            shutil.copyfile(entry["from"], target)


@pytest.mark.parametrize("case,exit_code,reward", [
    ("full", 0, 1.0), ("partial", 1, 2 / 3), ("missing", 1, 0.0),
    ("text", 1, 0.0), ("noop", 1, 0.0), ("legacy", 0, 1.0),
    ("malformed", 3, None), ("bad_pair", 3, None), ("bad_gold", 3, None),
    ("unknown_mode", 3, None),
])
def test_isolated_submission_and_verifier(train_record, tmp_path, case, exit_code, reward):
    work = tmp_path / "synthetic-train"
    work.mkdir()
    materialize(dataset._SPEC.setup(train_record)["files"], work)
    assert not (work / "gold.json").exists()
    assert not (work / "data").exists()
    if case in ("full", "partial", "missing"):
        indices = [7, 11] if case == "full" else [7] if case == "partial" else []
        artifact = prediction(train_record["gold"]["answer"], indices)
        (work / "submission.json").write_text(json.dumps(artifact))
        proc = subprocess.run([sys.executable, "qa/bin/qa", "submit-json", "@submission.json"],
                              cwd=work, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        # A stale text answer must not override the structured submission.
        (work / "answer.txt").write_text("wrong")
    elif case != "noop":
        (work / "answer.txt").write_text(train_record["gold"]["answer"])
    if case == "malformed":
        (work / "prediction.json").write_text("{")
    elif case == "bad_pair":
        (work / "prediction.json").write_text(json.dumps(prediction("red", [True])))
    verifier = dataset.verify_for(train_record, mode="legacy_answer_only" if case == "legacy"
                                  else "answer_support")
    # An actor-edited gold copy must be replaced by trusted VERIFY inputs.
    (work / "gold.json").write_text('{"answer":"wrong"}')
    materialize(verifier["inputs"], work)
    if case == "bad_gold":
        (work / "gold.json").write_text(json.dumps({"answer": "SYNTHETIC_GOLD red blue",
                                                    "aliases": [], "id": "broken"}))
    if case == "unknown_mode":
        verifier["argv"][verifier["argv"].index("--score-mode") + 1] = "unknown"
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=work,
                          env={**os.environ, "PYTHONPATH": str(work)}, capture_output=True,
                          text=True, timeout=15)
    assert proc.returncode == exit_code, proc.stdout + proc.stderr
    evidence = json.loads((work / base.EVIDENCE_REL).read_text())
    assert evidence["gold"] == {"redacted": True}
    assert "aliases" not in json.dumps(evidence)
    assert "supporting_facts" not in evidence["gold"]
    if reward is None:
        assert not (work / base.REWARD_REL).exists()
        assert evidence["measured"] is False
    else:
        assert float((work / base.REWARD_REL).read_text()) == pytest.approx(reward, abs=1e-6)
        assert evidence["measured"] is True
        assert evidence["official"]["support_f1"] == (None if case == "legacy" else reward)


def test_import_and_scorer_never_open_dataset_pool(tmp_path):
    # A fresh process guards the root before import, including QASpec.build().
    code = '''
import pathlib
original_open = pathlib.Path.open
def guarded_open(path, *args, **kwargs):
    if "/datasets/" in str(path):
        raise AssertionError("released payload access is forbidden")
    return original_open(path, *args, **kwargs)
pathlib.Path.open = guarded_open
import data.datasets.musique as dataset
assert dataset._SPEC._records == []
assert dataset._SPEC.load_error is None
gold = {"id":"synthetic-train", "answer":"red", "aliases":[],
        "supporting_facts":[["Title",7]]}
assert dataset.score_prediction({"schema_version":1,"answer":"red",
    "predicted_evidence":[["Title",7]]}, gold)["reward"] == 1.0
assert dataset._SPEC._records == []
'''
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("redact_marker", [False, True])
def test_shared_verifier_without_new_flags_retains_existing_semantics(
        train_record, tmp_path, redact_marker):
    # A fabricated adapter verifies compatibility without importing any other
    # released adapter or reading its table.
    verifier = base.QASpec.verifier(dataset._SPEC, train_record)
    materialize(verifier["inputs"], tmp_path)
    inert_adapter = (
        f"REDACT_EVIDENCE = {redact_marker!r}\n"
        "def score(answer, gold):\n"
        "    return {'reward': float(answer == gold['answer']), 'official': {'metric':'inert'}}\n"
    )
    # `--dataset inert_qa` resolves through `registry.module_name`, which puts an
    # adapter under `data.datasets.*`. Writing the fabricated adapter at the old flat
    # `data/inert_qa.py` made the check fail with ModuleNotFoundError for
    # `data.datasets.inert_qa` -- the same path construction the split changed, in a
    # test that is about the shared verifier rather than about the layout.
    inert_path = tmp_path / "data" / "datasets" / "inert_qa.py"
    inert_path.parent.mkdir(parents=True, exist_ok=True)
    inert_path.write_text(inert_adapter)
    (tmp_path / "answer.txt").write_text(train_record["gold"]["answer"])
    verifier["argv"][verifier["argv"].index("--dataset") + 1] = "inert_qa"
    assert "--score-mode" not in verifier["argv"]
    assert "--redact-gold" not in verifier["argv"]
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(tmp_path)},
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    evidence = json.loads((tmp_path / base.EVIDENCE_REL).read_text())
    assert evidence["reward"] == 1.0
    assert evidence["gold"] == ({"subset": None, "category": None, "redacted": True}
                                 if redact_marker else train_record["gold"])
