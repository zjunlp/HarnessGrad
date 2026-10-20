"""GSM8K scorer provenance and adapter regressions using synthetic answers.

The historical filename is retained for test discovery. The pinned integer-only
fixture preserves the inherited repository reference, whose upstream attribution
was unverified. The adapter uses a separate decimal-aware-extract identity and an
exclusive 1e-6 numeric tolerance. Agreement on integers does not prove upstream
scorer equivalence; explicit decimal cases pin the intended difference.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_FIXTURE = ROOT / "tests/fixtures/gsm8k_original/integer_extractor.json"
ORIGINAL_SHA256 = "45c73aeb817878b5ba65709f45766f21eb7899182df4eba9a8101e9169d8276b"
ORIGINAL = json.loads(ORIGINAL_FIXTURE.read_text())
REF_FLEXIBLE = re.compile(ORIGINAL["regex_pattern"])


def ref_flexible_extract(text: str) -> str:
    """Apply the immutable original integer-only reference, independently of data.datasets.gsm8k."""
    matches = REF_FLEXIBLE.findall(text)
    if not matches:
        return ""
    extracted = matches[-1]
    for character in ORIGINAL["strip_characters"]:
        extracted = extracted.replace(character, "")
    return extracted


@pytest.fixture(scope="module")
def gsm8k(synthetic_gsm8k):
    return synthetic_gsm8k


def test_original_integer_only_fixture_is_immutable():
    assert hashlib.sha256(ORIGINAL_FIXTURE.read_bytes()).hexdigest() == ORIGINAL_SHA256
    assert ORIGINAL["source_commit"] == "f7ab62143108e120914b5ead34ed339d0c785ee9"
    assert ORIGINAL["source_sha256"] == \
        "feb85c6646873e13e6a84feab4322b186e45e07c400151518d00f9e0e3765dcc"
    assert REF_FLEXIBLE.pattern == r"(-?[$]?\d[\d,]*)"
    assert ORIGINAL["match_selection"] == "last"
    assert ORIGINAL["strip_characters"] == [",", "$"]
    assert ORIGINAL["upstream_equivalence"] == "unverified historical attribution"


@pytest.mark.parametrize("text,extracted", [
    ("18", "18"),
    ("The answer is 18", "18"),
    ("$1,250", "1250"),
    ("-$1,250", "-1250"),
    ("-3", "-3"),
    ("#### 42", "42"),
    ("#### 7,000", "7000"),
    ("First 8, then 19, finally -3", "-3"),
    ("no number here", ""),
    ("", ""),
])
def test_adapter_and_original_agree_on_integer_extraction(gsm8k, text, extracted):
    assert ref_flexible_extract(text) == extracted
    assert gsm8k.official_flexible(text) == extracted


@pytest.mark.parametrize("text,original,decimal", [
    ("18.0", "0", "18.0"),
    ("3.5", "5", "3.5"),
    ("-3.25", "25", "-3.25"),
    ("$1,250.75", "75", "1250.75"),
    ("-$1,250.75", "75", "-1250.75"),
    (".5", "5", ".5"),
    ("-.5", "5", "-.5"),
    ("$.5", "5", ".5"),
    ("-$.5", "5", "-.5"),
    ("Worked with 18, then concluded -.5 dollars", "5", "-.5"),
    ("Worked with 18, then concluded -3.25", "25", "-3.25"),
    ("#### 3.5", "5", "3.5"),
])
def test_decimal_adapter_intentionally_differs_from_original(gsm8k, text, original, decimal):
    assert ref_flexible_extract(text) == original
    assert gsm8k.official_flexible(text) == decimal
    result = gsm8k.score(text, {"answer": decimal})
    assert result["reward"] == 1.0
    assert result["official"]["filter"] == "decimal-aware-extract"
    assert result["official"]["flexible_extract"] == decimal
    assert "decimal-aware-extract" in result["why"]
    assert gsm8k.numeric_equal(original, decimal) is False


@pytest.mark.parametrize("prediction,reference,expected", [
    ("18.0", "18", True),
    ("18.0000005", "18", True),
    ("18.000002", "18", False),
    ("0.0000009", "0", True),
    ("0.000001", "0", False),
    ("-0.000001", "0", False),
    ("", "18", False),
    ("unparseable", "18", False),
    ("nan", "18", False),
    ("inf", "18", False),
])
def test_adapter_comparison_preserves_exclusive_numeric_tolerance(
        gsm8k, prediction, reference, expected):
    assert gsm8k.numeric_equal(prediction, reference) is expected


def test_adapter_comparison_is_distinct_from_string_and_float_exact_match(gsm8k):
    assert "18.0" != "18"
    assert gsm8k.numeric_equal("18.0", "18") is True
    assert float("18.0000005") != float("18")
    assert gsm8k.numeric_equal("18.0000005", "18") is True


@pytest.mark.parametrize("text,extracted", [
    ("18", ""),
    ("#### 42", "42"),
    ("#### 7,000", "7000"),
    ("#### -3.25", "-3.25"),
    ("#### 18.0", "18.0"),
    ("#### 2 then #### 3.5", "2"),
])
def test_adapter_strict_marker_extraction_uses_first_marker(gsm8k, text, extracted):
    assert gsm8k.official_strict(text) == extracted


def test_bare_answer_and_strict_marker_differ_and_metric_is_named(gsm8k):
    result = gsm8k.score("18", {"answer": "18"})
    assert result["reward"] == 1.0
    assert result["official"]["filter"] == "decimal-aware-extract"
    assert result["official"]["strict_match"] == ""
    assert gsm8k.official_strict("#### 18") == "18"


def test_wrong_number_and_missing_number_are_measured_zeros(gsm8k):
    wrong = gsm8k.score("19", {"answer": "18"})
    assert wrong["reward"] == 0.0 and wrong["official"]["flexible_extract"] == "19"
    missing = gsm8k.score("I could not solve it", {"answer": "18"})
    assert missing["reward"] == 0.0 and missing["official"]["flexible_extract"] == ""


@pytest.mark.parametrize("answer", ["18", "3.5", "-3.25", "7000"])
def test_scorer_round_trip_on_synthetic_solution_fields(gsm8k, answer):
    row = {"answer": f"Synthetic working.\n#### {answer}"}
    gold = gsm8k._gold(row)
    assert gold["answer"] == answer
    assert gsm8k.score(answer, gold)["reward"] == 1.0
    assert gsm8k.official_strict(gold["verbatim"]) == answer
    assert gsm8k.score("999999", gold)["reward"] == 0.0


def test_goal_names_the_adapter_metric_and_accepts_equivalent_decimal_forms(gsm8k):
    goal = gsm8k._goal("Synthetic question", {"answer": "18"})
    assert "decimal-aware-extract" in goal
    assert "flexible-extract" not in goal
    assert "18.0" in goal
    assert gsm8k.score("18.0", {"answer": "18"})["reward"] == 1.0
