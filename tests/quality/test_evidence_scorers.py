"""The transcribed FEVER and HotpotQA scorers, pinned against upstream's own arithmetic.

These lived inside `test_dataset_refusals.py` because the two benchmarks were once refusal
candidates: their headline numbers need a field the QA answer contract did not carry. The
contract now carries structured evidence, so the datasets are ordinary -- but the transcription
still has to be pinned, since `fever_is_correct_label`'s missing `.strip()` and
`hotpot_answer_only_metrics`'s unprefixed F1 are the shortcuts a "fix" would take.
"""
from __future__ import annotations

import pytest

def test_fever_label_accuracy_is_the_official_comparison():
    from data.scaffolds.qa.evidence_scorers import fever_is_correct_label
    assert fever_is_correct_label("SUPPORTS", "supports") is True
    assert fever_is_correct_label("REFUTES", "SUPPORTS") is False
    # Upstream is `.upper()` and nothing else: a trailing period is a miss. Pinned because
    # "fixing" it would produce a number higher than the published one.
    assert fever_is_correct_label("NOT ENOUGH INFO", "NOT ENOUGH INFO.") is False


def test_fever_strict_needs_evidence_and_not_enough_info_is_exempt():
    from data.scaffolds.qa.evidence_scorers import (MissingEvidence,
                                          fever_is_strictly_correct)
    evidence = [[[None, None, "Page", 0], [None, None, "Page", 1]]]
    # NEI is evidence-exempt upstream: the branch returns on the label alone.
    assert fever_is_strictly_correct("NOT ENOUGH INFO", "NOT ENOUGH INFO", evidence, []) is True
    # A verifiable claim with no predicted evidence cannot be scored at all.
    with pytest.raises(MissingEvidence):
        fever_is_strictly_correct("SUPPORTS", "SUPPORTS", evidence, [])
    # With the evidence present, the group must be fully contained in the first five.
    assert fever_is_strictly_correct("SUPPORTS", "SUPPORTS", evidence,
                                     [["Page", 0], ["Page", 1]]) is True
    assert fever_is_strictly_correct("SUPPORTS", "SUPPORTS", evidence,
                                     [["Page", 0]]) is False


def test_hotpot_supporting_facts_require_an_exact_set():
    from data.scaffolds.qa.evidence_scorers import MissingEvidence, hotpot_update_sp
    gold = [["Arthur's Magazine", 0], ["2014–15 season", 1]]
    with pytest.raises(MissingEvidence):
        hotpot_update_sp(None, gold)
    perfect = hotpot_update_sp(gold, gold)
    assert perfect.em == 1.0 and perfect.prec == 1.0 and perfect.recall == 1.0
    partial = hotpot_update_sp([["Arthur's Magazine", 0]], gold)
    assert partial.em == 0.0, "em is exact set equality, not F1"
    assert 0.0 < partial.recall < 1.0


def test_hotpot_joint_is_the_product_the_leaderboard_reports():
    from data.scaffolds.qa.evidence_scorers import hotpot_joint, hotpot_update_sp
    gold = [["A", 0]]
    sp = hotpot_update_sp(gold, gold)
    joint_em, joint_f1 = hotpot_joint(1.0, 1.0, 1.0, sp)
    assert (joint_em, joint_f1) == (1.0, 1.0)
    # A perfect answer with no supporting facts is a joint 0 -- which is why answer-only
    # EM cannot stand in for the leaderboard number.
    empty_sp = hotpot_update_sp([["B", 0]], gold)
    assert hotpot_joint(1.0, 1.0, 1.0, empty_sp)[0] == 0.0


def test_hotpot_answer_only_metrics_are_computable_and_distinct():
    """The half that *would* work, exercised so the transcription is known to run."""
    from data.scaffolds.qa.evidence_scorers import hotpot_exact_match, hotpot_f1_score
    assert hotpot_exact_match("Arthur's Magazine", "Arthur's Magazine") == 1.0
    assert hotpot_exact_match("the magazine", "a magazine") == 1.0
    f1, precision, recall = hotpot_f1_score("yes", "yes")
    assert (f1, precision, recall) == (1.0, 1.0, 1.0)
    # The three-tuple shape is upstream's and is kept: a caller unpacking
    # (em, f1, prec) from a differently-ordered function would use f1 as precision and
    # every number on the page would still look normal.
    assert hotpot_f1_score("yes", "no") == (0.0, 0.0, 0.0)
