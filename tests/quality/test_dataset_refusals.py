"""The datasets that refuse, and why a refusal has to be provable.

`data/datasets/sotuqa.py`, `data/datasets/api_bank.py`, `data/datasets/nestful.py` and `data/datasets/tau2_bench.py` do not load.
Each refuses because its official metric or interaction contract is not reproducible on the
current platform. FEVER and HotpotQA now have a structured evidence answer contract and are
no longer refusal cases.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REFUSING = ["sotuqa", "api_bank", "tau2_bench", "nestful"]


@pytest.fixture(params=REFUSING)
def refused(request):
    import importlib
    return importlib.import_module(f"data.datasets.{request.param}")


def test_the_refusal_is_declared_and_load_raises_it(refused):
    reason = getattr(refused, "REFUSED", None)
    assert isinstance(reason, str) and len(reason) > 40, refused.__name__
    with pytest.raises(ValueError) as excinfo:
        refused.load()
    assert reason in str(excinfo.value), "load() must raise the declared reason"


#: The two *kinds* of refusal this repository has, and what each must say.
#:
#: A refusal is only credible if it names the thing that is missing. There are two shapes, and
#: forcing them into one assertion would either weaken it or exclude a real dataset:
#:
#: * **a contract gap** -- the metric needs a field the answer contract does not carry
#:   (`fever`'s predicted evidence, `hotpotqa`'s supporting facts, `sotuqa`'s judge);
#: * **an unreproducible metric** -- the metric needs something the *released data* does not
#:   carry, so even the gold solution cannot produce it (`api_bank`: executing the gold call
#:   reproduces the recorded result for only 51.6% of the suite).
#:
#: Each dataset must satisfy at least one, so a future refusal that says neither is caught.
REFUSAL_KINDS = {
    "contract_gap": ("evidence", "supporting", "judge", "answer channel", "contract"),
    "unreproducible": ("reproduc", "does not reproduce", "recorded result", "state the samples"),
}


def test_the_refusal_names_what_is_missing(refused):
    """A refusal has to say *what* is missing, not only that something is."""
    reason = refused.REFUSED.lower()
    kinds = [kind for kind, needles in REFUSAL_KINDS.items()
             if any(needle in reason for needle in needles)]
    assert kinds, (f"{refused.__name__} refuses without naming a contract gap or an "
                   f"unreproducible metric: {reason[:200]}")


def test_the_refusal_does_not_claim_the_metric_is_absent(refused):
    """The distinction the whole file rests on: reachable ≠ reachable *here*.

    Each reason has to point at what would make it reachable, so a reader does not conclude the
    benchmark is unscoreable. For a contract gap that is the contract change; for an
    unreproducible metric it is the subset or the redefinition the module docstring names.
    """
    reason = refused.REFUSED.lower()
    reachable = ("contract" in reason or "answer channel" in reason
                 or "path" in reason or "subset" in reason or "instead of" in reason
                 or "reproduc" in reason)
    assert reachable, reason


# --------------------------------------------------- the transcribed scorers ---


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


def test_the_refusing_modules_still_declare_the_dataset_contract(refused):
    """A refusal does not excuse the parts a program can still decide."""
    assert refused.KIND == "dataset"
    assert refused.DATASET_ROOT.is_absolute()
    downloads = refused.downloads()
    assert downloads and all(entry.get("version") for entry in downloads)
    for entry in downloads:
        assert len(str(entry.get("note") or "")) >= 20
