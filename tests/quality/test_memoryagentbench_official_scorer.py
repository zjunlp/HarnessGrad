"""Local scorer controls and optional fixed-upstream differential tests.

MAB_UPSTREAM points to the complete pinned official checkout. These tests never
fetch it; absent source skips only the reference comparisons, not local controls.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data.datasets.memoryagentbench.lib import scoring

ICL = "icl_banking77_5900shot_balance"
RECSYS = "recsys_redial_full"
MOVIES = {"/films/The_Matrix_(1999)": 1, "/films/Alien_(1979)": 2,
          "/films/Alien_(2003)": 3, "/films/Cat": 4, "/films/Bat": 5}


def test_exact_source_inventory_and_literal_star():
    assert len(scoring.PRIMARY_METRICS) == 22
    with pytest.raises(ValueError, match="unknown"):
        scoring.score_question("longmemeval_s_foo", "ok", ["ok"])


@pytest.mark.parametrize("prediction,expected", [
    ("43", 1), ("label: 43", 0), ("Answer: 43", 1),
    ("answer: Answer: 43", 1), ("thought\nAnswer: 43", 1),
    ("43\nnot the answer", 1), ("", 0), ("wrong", 0),
])
def test_icl_keeps_official_format_semantics(prediction, expected):
    result = scoring.score_question(ICL, prediction, ["43"])
    assert result["status"] == "measured"
    assert result["metric"] == "exact_match"
    assert result["score"] == expected


def test_default_max_and_alias_normalization():
    result = scoring.score_question("detective_qa", "Explanation\nAnswer: The Paris!", [["London"], ["Paris"]])
    assert result["score"] == 1
    assert result["metrics"]["f1"] == 1
    assert scoring.score_question("detective_qa", "PARIS", "the Paris")['score'] == 1
    # Default processing retains the full output if it scores better than line one.
    raw = scoring.local_metrics("ruler_qa1_197K", "intro\nParis", ["Paris"])
    assert raw['substring_exact_match'] is True
    assert raw['f1'] == pytest.approx(2 / 3)


def test_special_yes_no_f1():
    assert scoring.local_metrics("detective_qa", "yes indeed", ["yes"])["f1"] == 0


def test_eventqa_primary_any_alias_differs_from_all_elements_diagnostic():
    result = scoring.score_question("eventqa_full", "Answer: rain", ["rain", "snow"])
    assert result["score"] == 1
    assert result["metrics"]["eventqa_recall"] == 0
    result = scoring.local_metrics("eventqa_full", "rain\nsnow", ["rain", "snow"])
    assert result["eventqa_recall"] == 1
    assert result["exact_match"] is True  # Only the first line is parsed.


@pytest.mark.parametrize("source", ["detective_qa", *scoring.JUDGE_SOURCES])
def test_missing_prediction_has_no_score(source):
    result = scoring.score_question(source, None, ["gold"])
    assert result == {"status": "unmeasured", "metric": scoring.PRIMARY_METRICS[source],
                      "reason": "missing_prediction"}


@pytest.mark.parametrize("source", sorted(scoring.JUDGE_SOURCES))
def test_judge_cannot_be_replaced_with_overlap(source):
    result = scoring.score_question(source, "gold", ["gold"])
    assert result == {"status": "unmeasured", "metric": scoring.PRIMARY_METRICS[source],
                      "reason": "judge_required"}
    assert scoring.local_metrics(source, "gold", ["gold"])["exact_match"] is True


@pytest.mark.parametrize("bad", [None, [], "", " \n", [""], [[]], [1], ["gold", ["other"]], [[["gold"]]]])
def test_invalid_gold_is_rejected_even_for_missing_prediction(bad):
    with pytest.raises(ValueError, match="answers"):
        scoring.score_question("detective_qa", None, bad)


@pytest.mark.parametrize("source", ["invented", "longmemeval_s_-1_500", "ruler_niah", None, []])
def test_unknown_source_rejected(source):
    with pytest.raises(ValueError, match="unknown"):
        scoring.local_metrics(source, "gold", ["gold"])


@pytest.mark.parametrize("bad", [1, [], {}])
def test_invalid_prediction_rejected(bad):
    with pytest.raises(ValueError, match="prediction"):
        scoring.score_question("detective_qa", bad, ["gold"])


def test_local_missing_prediction_is_not_a_blank():
    with pytest.raises(ValueError, match="prediction"):
        scoring.local_metrics("detective_qa", None, ["gold"])
    assert scoring.score_question("detective_qa", "", ["gold"])["score"] == 0


def test_recommendation_mapping_is_explicit_and_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="entity2id"):
        scoring.score_question(RECSYS, "1. Alien", ["2"])
    result = scoring.score_question(RECSYS, "1. Alien\n2. The Matrix", ["2", "3"], entity2id=MOVIES)
    # Distinct IDs that normalize to the same movie count independently, as upstream.
    assert result['score'] == 1
    assert result['metrics']['recsys_recall@1'] == 1
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="missing"):
        scoring.score_question(RECSYS, "Alien", ["999"], entity2id=MOVIES)
    with pytest.raises(ValueError, match="integer movie IDs"):
        scoring.score_question(RECSYS, "Alien", ["2,3"], entity2id=MOVIES)


def test_recommendation_duplicate_id_uses_last_entry():
    mapping = {"old": 1, "new": 1, "other": 2}
    assert scoring.score_question(RECSYS, "1.new", ["1"], entity2id=mapping)['score'] == 1


def test_blank_recommendation_preserves_nearest_movie_behavior():
    # A blank recommendation is still matched; never force empty output to zero.
    result = scoring.score_question(RECSYS, "", ["1"], entity2id={"one": 1})
    assert result["score"] == 1


def test_execution_failure_does_not_become_zero(monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic scorer failure")
    monkeypatch.setattr(scoring, "_rouge_scorer", unavailable)
    with pytest.raises(RuntimeError, match="synthetic scorer failure"):
        scoring.score_question("detective_qa", "gold", ["gold"], diagnostics=True)


def test_import_needs_no_third_party_packages():
    module = Path(scoring.__file__).resolve()
    script = "import runpy,sys; runpy.run_path(sys.argv[1]); assert 'numpy' not in sys.modules; assert 'nltk' not in sys.modules"
    subprocess.run([sys.executable, "-I", "-S", "-c", script, str(module)], check=True)


@pytest.fixture(scope="module")
def official():
    checkout = os.environ.get("MAB_UPSTREAM")
    if not checkout:
        pytest.skip("MAB_UPSTREAM is unset; fixed official source is required for differential tests")
    path = Path(checkout) / "utils" / "eval_other_utils.py"
    if not path.is_file():
        pytest.fail(f"MAB_UPSTREAM does not contain the official scorer: {path}")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == scoring.UPSTREAM_SCORER_SHA256
    spec = importlib.util.spec_from_file_location("_mab_official_scorer_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("source", sorted(scoring.PRIMARY_METRICS))
@pytest.mark.parametrize("prediction", ["gold", "", "wrong", "Answer: gold", "thought\ngold"])
def test_fixed_official_local_metrics_match(official, source, prediction, tmp_path, monkeypatch):
    answers = ["gold", "other"]
    mapping = None
    if source == RECSYS:
        answers = ["2", "3"]
        mapping = MOVIES
        relative = tmp_path / "processed_data" / "Recsys_Redial"
        relative.mkdir(parents=True)
        (relative / "entity2id.json").write_text(json.dumps(mapping))
        monkeypatch.chdir(tmp_path)
    expected, _ = official.post_process({"output": prediction}, answers, {"sub_dataset": source})
    actual = scoring.local_metrics(source, prediction, answers, entity2id=mapping)
    assert actual == expected


@pytest.mark.parametrize("prediction", ["label: 43", "Answer: 43", "thought\nAnswer: 43", "43\n43"])
def test_fixed_official_icl_format_match(official, prediction):
    expected, _ = official.post_process({"output": prediction}, ["43"], {"sub_dataset": ICL})
    assert scoring.local_metrics(ICL, prediction, ["43"]) == expected


@pytest.mark.parametrize("prediction", ["1. Hat", "1. Alien\n2. Cat", "", "Alien, Cat"])
def test_fixed_official_recommendation_ties_and_duplicate_names(official, prediction, tmp_path, monkeypatch):
    relative = tmp_path / "processed_data" / "Recsys_Redial"
    relative.mkdir(parents=True)
    (relative / "entity2id.json").write_text(json.dumps(MOVIES))
    monkeypatch.chdir(tmp_path)
    expected, _ = official.post_process({"output": prediction}, ["2", "3", "4"], {"sub_dataset": RECSYS})
    assert scoring.local_metrics(RECSYS, prediction, ["2", "3", "4"], entity2id=MOVIES) == expected


def test_stdlib_distance_matches_official_dependency_including_unicode():
    import editdistance
    import random
    rng = random.Random(29)
    pairs = [("", ""), ("é🐈", "e猫"), ("a" * 500, "a" * 499 + "b")]
    for _ in range(300):
        pairs.append(tuple("".join(rng.choices("abc猫é🐈", k=rng.randrange(100))) for _ in range(2)))
    for left, right in pairs:
        assert scoring._edit_distance(left, right) == editdistance.eval(left, right)


@pytest.mark.parametrize("source", sorted(set(scoring.PRIMARY_METRICS) - scoring.JUDGE_SOURCES))
@pytest.mark.parametrize("prediction", ["gold", "", "Answer: gold", "wrong\ngold"])
def test_primary_only_score_matches_official_without_rouge(official, source, prediction, tmp_path, monkeypatch):
    answers, mapping = ["gold"], None
    if source == RECSYS:
        answers, mapping = ["2", "3"], MOVIES
        relative = tmp_path / "processed_data/Recsys_Redial"
        relative.mkdir(parents=True)
        (relative / "entity2id.json").write_text(json.dumps(mapping))
        monkeypatch.chdir(tmp_path)
    expected, _ = official.post_process({"output": prediction}, answers, {"sub_dataset": source})
    monkeypatch.setattr(scoring, "_rouge_scorer", lambda: pytest.fail("primary scorer loaded ROUGE"))
    actual = scoring.score_question(source, prediction, answers, entity2id=mapping)
    assert actual["score"] == expected[scoring.PRIMARY_METRICS[source]]
