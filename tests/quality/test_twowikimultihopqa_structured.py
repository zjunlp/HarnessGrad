"""Synthetic differential gates; no released rows, aliases, mirrors or models are read."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data.scaffolds.qa import base  # noqa: E402

# QASpec.build normally constructs the provisioned pool even during import. Block that
# loader before importing, rather than assuming release files are absent on this host.
with patch.object(base.QASpec, "records", return_value=[]):
    dataset = importlib.import_module("data.datasets.twowikimultihopqa")

ALIASES = {
    "Q_answer": ["Crimson City", "Crimson Citizens"],
    "Q_subject": ["S Alias", "S People"],
    "Q_object": ["O Alias"],
}


@pytest.fixture(autouse=True)
def no_release_reads(monkeypatch):
    def forbidden():
        raise AssertionError("release payload reads are forbidden in synthetic tests")

    monkeypatch.setattr(dataset, "_rows", forbidden)
    monkeypatch.setattr(dataset, "_aliases", lambda: deepcopy(ALIASES))
    monkeypatch.setattr(dataset._SPEC, "_rows", forbidden)


def row(identifier="fabricated", **updates):
    result = {
        "_id": identifier, "question": "Which city?", "type": "synthetic",
        "answer": "red blue blue", "answer_id": "",
        "supporting_facts": [["Page One", 0], ["Page Two", 1]],
        "evidences": [["Subject", "born in", "Object"],
                      ["Other", "located in", "Place"]],
        "evidences_id": [],
        "context": [["Page One", ["Public sentence zero.", "Public sentence one."]]],
    }
    result.update(updates)
    return result


def prediction(**updates):
    return {"schema_version": 1, **updates}


def official_metrics(rows, predictions, tmp_path, capsys):
    """Run the unmodified, verbatim upstream file evaluator on fabricated JSON files.

    This path independently expands aliases, detects missing channels and aggregates;
    it never calls the new wrapper or its gold conversion.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    gold_file, pred_file, alias_file = [tmp_path / name for name in
                                       ("gold.json", "pred.json", "aliases.jsonl")]
    gold_file.write_text(json.dumps(rows))
    upstream = {"answer": {}, "sp": {}, "evidence": {}}
    for identifier, pred in predictions.items():
        for public, official in (("answer", "answer"), ("supporting_facts", "sp"),
                                 ("evidence", "evidence")):
            if public in pred:
                upstream[official][identifier] = pred[public]
    pred_file.write_text(json.dumps(upstream))
    alias_file.write_text("\n".join(json.dumps({
        "Q_id": key, "aliases": values[:-1], "demonyms": values[-1:],
    }) for key, values in ALIASES.items()))
    dataset.eval(pred_file, gold_file, alias_file)
    output = capsys.readouterr().out
    return json.loads(output[output.index("{"):])


def differential(rows, predictions, tmp_path, capsys):
    expected = official_metrics(rows, predictions, tmp_path, capsys)
    actual = dataset.evaluate_predictions(predictions, [dataset._gold(r) for r in rows])
    assert set(actual) == set(dataset.METRIC_KEYS) == set(expected)
    assert actual == expected
    return actual


def test_verbatim_upstream_region_is_unchanged():
    # Pin the evaluator region independently of the adapter wrapper. A drift cannot be
    # hidden by making both differential paths call a newly edited upstream function.
    source = (ROOT / "data/datasets/twowikimultihopqa.py").read_text()
    region = source[source.index("def normalize_answer(s):"):
                    source.index("# ------------------------------------------------------------------- the platform")]
    assert hashlib.sha256(region.encode()).hexdigest() == "e18ccdf4b87d16c30e03a62b1dc9d0906c0b0130ae5eace17885d2a9ea1b13c9"


def test_duplicate_answer_words_and_support_sets(tmp_path, capsys):
    r = row()
    pred = prediction(answer="red red blue", supporting_facts=[
        ["PAGE ONE", 0], ["PAGE ONE", 0], ["wrong", 2]], evidence=r["evidences"])
    values = differential([r], {r["_id"]: pred}, tmp_path, capsys)
    assert values["f1"] == 66.67  # Counter overlap, not token-set overlap.
    assert values["sp_f1"] == 50.0  # Exact duplicate support pairs collapse.
    assert values["evi_em"] == 100.0
    assert values["joint_em"] == 0.0


def test_support_case_normalization_occurs_after_deduplication(tmp_path, capsys):
    r = row()
    pred = prediction(answer=r["answer"], supporting_facts=[
        ["PAGE ONE", 0], ["Page One", 0]], evidence=r["evidences"])
    values = differential([r], {r["_id"]: pred}, tmp_path, capsys)
    assert values["sp_f1"] == 80.0  # Preserve upstream's pre-normalization set quirk.


def test_evidence_triples_normalization_aliases_and_input_immutability(tmp_path, capsys):
    r = row(answer="Canonical Answer", answer_id="Q_answer",
            evidences=[["Subject", "born in", "Object"]],
            evidences_id=[["Q_subject", "born in", "Q_object"]])
    pred = prediction(answer="Crimson Citizens", supporting_facts=r["supporting_facts"],
                      evidence=[["S PEOPLE!", "Born  in", "O ALIAS."],
                                ["s people", "born in", "o alias"]])
    gold = dataset._gold(r)
    assert set(gold["evidence_aliases"]) == {"Q_subject", "Q_object"}
    snapshots = deepcopy((pred, gold, r))
    with patch.object(dataset, "_aliases", side_effect=AssertionError("verifier alias read")):
        verdict = dataset.score_prediction(pred, gold)
    assert (pred, gold, r) == snapshots
    assert verdict["reward"] == 1.0
    assert verdict["correct"] is True
    values = differential([r], {r["_id"]: pred}, tmp_path, capsys)
    assert all(value == 100.0 for value in values.values())


def test_evidence_does_not_strip_articles_or_reverse_triples(tmp_path, capsys):
    r = row(evidences=[["The Subject", "owns", "Object"]])
    for index, triple in enumerate((["Subject", "owns", "Object"],
                                    ["Object", "owns", "The Subject"])):
        pred = prediction(answer=r["answer"], supporting_facts=r["supporting_facts"],
                          evidence=[triple])
        values = differential([r], {r["_id"]: pred}, tmp_path / str(index), capsys)
        assert values["evi_f1"] == values["joint_f1"] == 0.0


def test_joint_f1_multiplies_precision_and_recall_not_component_f1s(tmp_path, capsys):
    r = row(answer="red blue", supporting_facts=[["Page", 0]])
    pred = prediction(answer="red", supporting_facts=[["Page", 0], ["wrong", 1]],
                      evidence=r["evidences"][:1])
    values = differential([r], {r["_id"]: pred}, tmp_path, capsys)
    assert values["joint_prec"] == 50.0
    assert values["joint_recall"] == 25.0
    assert values["joint_f1"] == 33.33
    verdict = dataset.score_prediction(pred, dataset._gold(r))
    official = verdict["official"]
    assert verdict["reward"] == pytest.approx(1 / 3)
    assert verdict["reward"] != pytest.approx(
        official["f1"] * official["sp_f1"] * official["evi_f1"])


def test_alias_variants_preserve_upstream_evidence_matching_quirk(tmp_path, capsys):
    r = row(evidences=[["Subject", "born in", "Object"]],
            evidences_id=[["Q_subject", "born in", "Q_object"]])
    pred = prediction(answer=r["answer"], supporting_facts=r["supporting_facts"],
                      evidence=[["Subject", "born in", "Object"],
                                ["S Alias", "born in", "Object"]])
    values = differential([r], {r["_id"]: pred}, tmp_path, capsys)
    assert values["evi_recall"] == 200.0  # Upstream matches predictions, not gold groups.
    assert values["evi_em"] == 0.0


def test_answer_alias_precision_and_recall_are_maximized_independently(tmp_path, capsys):
    r = row(answer="Crimson City Red Blue", answer_id="Q_answer")
    pred = prediction(answer="Crimson City Red", supporting_facts=r["supporting_facts"],
                      evidence=r["evidences"])
    values = differential([r], {r["_id"]: pred}, tmp_path, capsys)
    assert values["f1"] == 85.71
    assert values["prec"] == values["recall"] == values["joint_f1"] == 100.0
    assert values["joint_em"] == 0.0


@pytest.mark.parametrize("missing", ["answer", "supporting_facts", "evidence", "row"])
def test_missing_predictions_keep_the_entire_gold_denominator(missing, tmp_path, capsys):
    rows = [row("one"), row("two")]
    complete = prediction(answer=rows[0]["answer"],
                          supporting_facts=rows[0]["supporting_facts"],
                          evidence=rows[0]["evidences"])
    predictions = {"one": deepcopy(complete), "two": deepcopy(complete),
                   "extra": prediction(answer="ignored")}
    if missing == "row":
        del predictions["two"]
    else:
        del predictions["two"][missing]
    values = differential(rows, predictions, tmp_path, capsys)
    assert values["joint_em"] == values["joint_f1"] == 50.0
    prefixes = {"answer": "", "supporting_facts": "sp_", "evidence": "evi_"}
    for channel, prefix in prefixes.items():
        expected = 50.0 if missing in (channel, "row") else 100.0
        assert values[prefix + "f1"] == expected


def test_omitted_channels_differ_from_explicit_empty_sets(tmp_path, capsys):
    r = row(answer="", supporting_facts=[], evidences=[])
    omitted = differential([r], {r["_id"]: prediction()}, tmp_path / "missing", capsys)
    explicit = differential([r], {r["_id"]: prediction(
        answer="", supporting_facts=[], evidence=[])}, tmp_path / "empty", capsys)
    assert all(value == 0.0 for value in omitted.values())
    assert explicit["em"] == explicit["sp_em"] == explicit["evi_em"] == 100.0
    assert explicit["joint_em"] == 100.0
    assert explicit["joint_f1"] == 0.0


@pytest.mark.parametrize("answer,gold", [("The CITY!", "city"), ("yes city", "yes"),
                                        ("yes", "no"), ("", "")])
def test_answer_normalization_and_special_answers(answer, gold, tmp_path, capsys):
    r = row(answer=gold)
    differential([r], {r["_id"]: prediction(answer=answer)}, tmp_path, capsys)


@pytest.mark.parametrize("bad", [
    [], {}, {"schema_version": True}, prediction(answer=None),
    prediction(supporting_facts=[["Page", True]]),
    prediction(supporting_facts=[["Page", -1]]), prediction(supporting_facts=None),
    prediction(evidence=[["S", "R"]]), prediction(evidence=[["S", "R", 1]]),
    prediction(evidence=None), prediction(gold="secret"),
])
def test_malformed_contract_is_rejected(bad):
    with pytest.raises(ValueError):
        dataset.score_prediction(bad, dataset._gold(row()))


def test_missing_gold_is_a_scorer_error_and_empty_pool_is_rejected():
    with pytest.raises(ValueError, match="gold"):
        dataset.score_prediction(prediction(answer="x"), {"answer": "x"})
    with pytest.raises(ValueError, match="empty"):
        dataset.evaluate_predictions({}, [])


@pytest.mark.parametrize("ids", [
    [["Q_subject", "born in", "Q_object"]],
    [["Q_subject", "wrong relation", "Q_object"], ["", "located in", ""]],
])
def test_invalid_gold_evidence_ids_are_rejected(ids):
    with pytest.raises(ValueError, match="evidence"):
        dataset._gold(row(evidences_id=ids))


def test_legacy_mode_is_explicit_and_has_no_joint_claim():
    gold = dataset._gold(row())
    verdict = dataset.score(gold["answer"], gold)
    assert verdict["reward"] == 1.0
    assert verdict["official"]["mode"] == "legacy_answer_only"
    assert "legacy answer-only" in verdict["why"]
    assert not any(key.startswith(("sp_", "evi_", "joint_"))
                   for key in verdict["official"])
    partial = dataset.score(json.dumps(prediction(answer=gold["answer"])), gold)
    assert partial["official"]["mode"] == "structured"
    assert partial["official"]["f1"] == 1.0
    assert partial["reward"] == 0.0


def test_public_context_indices_and_trusted_gold_boundary():
    r = row(answer="Private answer", supporting_facts=[["Private support", 9]],
            evidences=[["Private subject", "Private relation", "Private object"]])
    gold = dataset._gold(r)
    goal = dataset._goal(dataset._question(r), gold)
    assert "[0] Public sentence zero." in goal
    assert "[1] Public sentence one." in goal
    assert "qa submit @submission.json" in goal
    for secret in ("Private answer", "Private support", "Private subject", "Crimson City"):
        assert secret not in goal
    record = {"task_id": "twowikimultihopqa/dev/0", "gold": gold}
    setup = dataset._SPEC.setup(record)
    assert all(entry["path"] != "gold.json" for entry in setup["files"])
    verifier = dataset._SPEC.verifier(record)
    trusted = next(entry for entry in verifier["inputs"] if entry["path"] == "gold.json")
    assert json.loads(trusted["content"]) == gold


@pytest.mark.parametrize("submission,expected", [("full", 1.0), ("partial", 0.0),
                                                 ("legacy", 1.0), ("noop", 0.0),
                                                 ("malformed", None), ("bad-type", None),
                                                 ("bad-key", None), ("bad-version", None),
                                                 ("loop-object", 1.0), ("loop-truncated", 1.0)])
def test_public_cli_to_materialized_verifier(submission, expected, tmp_path, monkeypatch):
    r = row()
    gold = dataset._gold(r)
    record = {"task_id": "twowikimultihopqa/dev/0", "gold": gold}
    spec = dataset._SPEC
    for entry in spec.setup(record)["files"] + spec.verifier(record)["inputs"]:
        target = tmp_path / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry["content"]) if "content" in entry else target.write_bytes(
            Path(entry["from"]).read_bytes())
    if submission != "noop":
        pred = prediction(answer=r["answer"])
        if submission in ("full", "loop-object", "loop-truncated"):
            pred.update(supporting_facts=r["supporting_facts"], evidence=r["evidences"])
        content = r["answer"] if submission == "legacy" else json.dumps(pred)
        if submission == "malformed":
            content = "{broken JSON"
        elif submission == "bad-type":
            content = json.dumps(prediction(answer="x", evidence=[["S", "R", 1]]))
        elif submission == "bad-key":
            content = json.dumps({**pred, "gold": "untrusted fake reference"})
        elif submission == "bad-version":
            content = json.dumps({"answer": r["answer"]})
        elif submission in ("loop-object", "loop-truncated"):
            loop_spec = importlib.util.spec_from_file_location(
                "materialized_submission_loop", ROOT / "base_harness/loop/agent.py")
            loop = importlib.util.module_from_spec(loop_spec)
            loop_spec.loader.exec_module(loop)
            replies = [json.dumps({"answer": pred})]
            if submission == "loop-truncated":
                replies.insert(0, json.dumps({"answer": json.dumps(pred)[:-1]}))
            replies = iter(replies)
            monkeypatch.setattr(loop, "MAX_STEPS", 2)
            monkeypatch.setattr(loop, "_complete", lambda _: next(replies))
            content = loop.run({"goal": "Submit a synthetic prediction."}, tmp_path)
            assert json.loads((tmp_path / "answer.txt").read_text()) == pred
        (tmp_path / "submission.json").write_text(content)
        submitted = subprocess.run(
            [sys.executable, "qa/bin/qa", "submit", "@submission.json"],
            cwd=tmp_path, capture_output=True, text=True)
        if submission == "malformed":
            assert submitted.returncode == 2, submitted.stderr
            assert not (tmp_path / "answer.txt").exists()
            # Direct writes can bypass the CLI. The verifier must still refuse
            # malformed JSON as unmeasured rather than inventing a scorer zero.
            (tmp_path / "answer.txt").write_text(content)
        else:
            assert submitted.returncode == 0, submitted.stderr
    # Suppress the shared scaffold's import-time pool construction in the child too.
    # The check and scorer run from their actual, unedited VERIFY copies.
    bootstrap = (
        "import runpy,sys; from data.scaffolds.qa import base; "
        "base.QASpec.records=lambda self: []; "
        "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    checked = subprocess.run(
        [sys.executable, "-c", bootstrap, *spec.verifier(record)["argv"][1:], "--redact-gold"],
        cwd=tmp_path, capture_output=True, text=True)
    if expected is None:
        assert checked.returncode == 3, checked.stderr
        assert not (tmp_path / "verifier/reward.txt").exists()
        evidence = json.loads((tmp_path / "verifier/evidence.json").read_text())
        assert evidence["measured"] is False
        assert evidence["gold"] == {"redacted": True}
        return
    assert checked.returncode == (0 if expected == 1.0 else 1), checked.stderr
    assert float((tmp_path / "verifier/reward.txt").read_text()) == expected
    evidence = json.loads((tmp_path / "verifier/evidence.json").read_text())
    assert evidence["measured"] is True
    assert evidence["gold"] == {"redacted": True}
    assert evidence["official"]["mode"] == (
        "structured" if submission in ("full", "partial", "loop-object", "loop-truncated")
        else "legacy_answer_only")
