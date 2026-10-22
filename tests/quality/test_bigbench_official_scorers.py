"""The official scorers for the three ReWOO/BIG-bench tables, copied in.

Three datasets, three upstream references, transcribed here so the claim "our reward is
the official metric" is checkable rather than asserted:

| dataset | reference | rule |
| --- | --- | --- |
| `strategyqa` | ReWOO `utils/Evaluator.py::normalize_answer` + `em` | normalised whole-string equality |
| `sportsunderstanding` | BIG-bench `sports_understanding` `multiple_choice_grade` | the correct label is named |
| `physicsquestions` | BIG-bench `physics_questions` `exact_str_match` | exact match against the target **list** |

The tables come from ReWOO's tarball (`data/scaffolds/qa/rewoo.py` pins it for all four datasets
that read it); `strategy_qa` no longer exists on BIG-bench main, so ReWOO's vendored copy
is both the reachable table and the one whose published numbers came from it.

Each dataset's `.py` documents what it deliberately does not do -- a numeric tolerance for
`physicsquestions`, an `f1` for `strategyqa`, a plain substring rule for
`sportsunderstanding`. This file pins those decisions too, because a silent change to any
of them changes a number without changing its label.
"""
from __future__ import annotations

import re
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------- ReWOO's normalizer ---


def ref_rewoo_normalize_answer(s: str) -> str:
    """ReWOO `utils/Evaluator.py::normalize_answer`, verbatim."""

    def remove_articles(text):
        return re.sub(r"\b(a|an|the)\b", " ", text)

    def white_space_fix(text):
        return " ".join(text.split())

    def remove_punc(text):
        return "".join(ch for ch in text if ch not in set(string.punctuation))

    def lower(text):
        return text.lower()

    return white_space_fix(remove_articles(remove_punc(lower(str(s)))))


NORMALIZE_CASES = ["Yes", "yes.", "YES", " No ", "a Yes!", "", "The Answer",
                   "yes, because it rains"]


@pytest.fixture(scope="module")
def strategyqa():
    import data.datasets.strategyqa as module
    if not module._RECORDS:
        pytest.skip("strategyqa is not provisioned")
    return module


@pytest.fixture(scope="module")
def sports():
    import data.datasets.sportsunderstanding as module
    if not module._RECORDS:
        pytest.skip("sportsunderstanding is not provisioned")
    return module


@pytest.fixture(scope="module")
def physics():
    import data.datasets.physicsquestions as module
    if not module._RECORDS:
        pytest.skip("physicsquestions is not provisioned")
    return module


# --------------------------------------------------------------- strategyqa ---


def test_strategyqa_normalize_matches_rewoo(strategyqa):
    for text in NORMALIZE_CASES:
        assert strategyqa.normalize_answer(text) == ref_rewoo_normalize_answer(text), text


def test_strategyqa_exact_match_compares_the_whole_answer(strategyqa):
    """`yes.` matches; `yes, because it rains` does not.

    That is ReWOO's rule -- whole-string equality after normalisation -- and it is why the
    goal tells the agent that an explanation turns a correct answer into a wrong one.
    """
    for prediction, want in [("Yes", "Yes"), ("yes.", "Yes"), ("YES!", "Yes"),
                             ("No", "Yes"), ("yes, because it rains", "Yes"),
                             ("Yes, because it rains", "Yes")]:
        assert strategyqa.exact_match(prediction, want) == \
            (ref_rewoo_normalize_answer(prediction) == ref_rewoo_normalize_answer(want)), \
            (prediction, want)
    assert strategyqa.exact_match("yes, because it rains", "Yes") is False


def test_strategyqa_labels_are_only_yes_and_no(strategyqa):
    assert {r["gold"]["answer"] for r in strategyqa._RECORDS} <= {"Yes", "No"}
    assert {str(r["row"]["target"]).strip() for r in strategyqa._RECORDS} <= {"Yes", "No"}


# ---------------------------------------------------------- sportsunderstanding ---


def test_sports_names_both_detects_a_non_choice(sports):
    both = "It could be plausible or implausible depending on the source."
    assert sports.names_both(both) is True
    assert sports.names_both("plausible") is False
    assert sports.names_both("This is implausible.") is False
    # The measured trap: `"implausible"` contains `"plausible"`, so a bare substring rule
    # scores it for a gold of `plausible`. Word boundaries are what stop that.
    assert sports.names_both("implausible") is False
    assert sports.substring_choice("implausible", "plausible") is False
    assert sports.substring_choice("implausible", "implausible") is True


def test_sports_substring_rule_matches_the_multiple_choice_grade(sports):
    """The rule and its one divergence, pinned together.

    `multiple_choice_grade` asks whether the correct choice was identified. The substring
    rule answers that for a single-label answer; for an answer naming **both** labels it
    would count either gold as correct, so this adapter refuses that case. The divergence
    is asserted here so that a future change to either side is visible.
    """
    assert sports.substring_choice("plausible", "plausible") is True
    assert sports.substring_choice("It seems implausible", "plausible") is False
    both = "could be plausible, could be implausible"
    assert sports.substring_choice(both, "plausible") is True
    assert sports.substring_choice(both, "implausible") is True
    for label in ("plausible", "implausible"):
        assert sports.score(both, {"answer": label})["reward"] == 0.0
    assert sports.score("plausible", {"answer": "plausible"})["reward"] == 1.0
    assert sports.score("IMPLAUSIBLE", {"answer": "implausible"})["reward"] == 1.0


def test_sports_targets_are_the_two_labels(sports):
    assert {r["gold"]["answer"] for r in sports._RECORDS} <= {"plausible", "implausible"}


# ----------------------------------------------------------- physicsquestions ---


def test_physics_parses_bigbench_target_lists(physics):
    assert physics.accepted_answers("Can be one of ['7187 m', '7 km']") == ["7187 m", "7 km"]
    assert physics.accepted_answers('Can be one of ["540000 kg*m/s"]') == ["540000 kg*m/s"]
    # An unrecognised encoding raises rather than becoming a raw-string comparison that
    # scores every answer 0 for a reason nothing records.
    with pytest.raises(ValueError, match="Can be one of"):
        physics.accepted_answers("7187 m")


def test_physics_exact_str_match_is_not_a_numeric_tolerance(physics):
    """`7.2 km` is **not** accepted for a target of `7 km`, and that is deliberate.

    A relative tolerance is a defensible scorer for "is the physics right?"; it is not
    BIG-bench's `exact_str_match`, and the two give different numbers on the same
    transcripts. This pins the stricter rule.
    """
    assert physics.exact_str_match("7187 m", "7187 m") is True
    assert physics.exact_str_match(" 7187 m ", "7187 m") is True
    assert physics.exact_str_match("7 km", "7 km.") is True
    assert physics.exact_str_match("7.2 km", "7 km") is False
    gold = {"answer": "7187 m", "accepted": ["7187 m", "7 km"]}
    assert physics.score("7 km", gold)["reward"] == 1.0
    assert physics.score("7.2 km", gold)["reward"] == 0.0
    assert physics.score("7187 m", gold)["official"]["metric"].startswith("exact_str_match")


def test_physics_has_53_rows_and_the_small_n_is_stated(physics):
    assert len(physics._RECORDS) == 53
    assert "53" in physics.__doc__, "the row count must travel with the dataset"
