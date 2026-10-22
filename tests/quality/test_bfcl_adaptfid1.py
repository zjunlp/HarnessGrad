"""Fabricated BFCL parse regressions; never read a released task pool."""
from data.datasets import bfcl_v3 as dataset

import pytest


@pytest.mark.parametrize("text", [
    "there is no suitable function",
    "No function is needed for this request.",
    "I can answer without using a tool.\nThere is no suitable function.",
    "```text\nthere is no suitable function\n```",
    "123invalid()",
])
def test_irrelevance_prose_without_an_identifier_call_is_no_call(text):
    verdict = dataset.score(text, {"functions": [], "subset": "irrelevance"})
    assert verdict["reward"] == 1.0
    assert verdict["official"]["predicted_functions"] == []
    assert dataset.score(text, {"functions": ["fabricated_tool"], "subset": "simple"})[
        "reward"] == 0.0


@pytest.mark.parametrize("text", [
    "", " \n\t", "NONE", "none", "No tool", "NO_FUNCTION", "no-tool", "nofunction",
])
def test_documented_empty_and_no_tool_markers_remain_no_call(text):
    assert dataset.parse_prediction(text) == []
    assert dataset.score(text, {"functions": [], "subset": "irrelevance"})["reward"] == 1.0


@pytest.mark.parametrize("text,expected", [
    ("fabricated_tool", ["fabricated_tool"]),
    ("namespace.fabricated_tool", ["namespace.fabricated_tool"]),
    ("fabricated_tool,\nother_tool;", ["fabricated_tool", "other_tool"]),
    ("fabricated_tool(value=1)", ["fabricated_tool(value=1)"]),
    ("Calling namespace.fabricated_tool(value=1).", ["Calling namespace.fabricated_tool(value=1)."]),
    ("fabricated_tool(); other_tool()", ["fabricated_tool(); other_tool()"]),
    ("NONE\nfabricated_tool", ["fabricated_tool"]),
    ("No suitable function.\nfabricated_tool", ["fabricated_tool"]),
])
def test_named_tools_and_identifier_calls_still_reject_irrelevance(text, expected):
    assert dataset.parse_prediction(text) == expected
    assert dataset.score(text, {"functions": [], "subset": "irrelevance"})["reward"] == 0.0


def test_names_keep_namespace_normalization_case_and_multiplicity():
    gold = {"functions": ["fabricated_tool"], "subset": "simple"}
    assert dataset.score("namespace.fabricated_tool", gold)["reward"] == 1.0
    assert dataset.score("Fabricated_tool", gold)["reward"] == 0.0
    assert dataset.score("fabricated_tool\nfabricated_tool", gold)["reward"] == 0.0
    assert dataset.score("fabricated_tool(value=1)", gold)["reward"] == 0.0


@pytest.mark.parametrize("prediction", [
    "wrong_tool(); namespace.expected_tool",
    "wrong_tool(value=namespace.expected_tool",
    "Calling wrong_tool(). namespace.expected_tool",
    "namespace.expected_tool()",
])
def test_call_shaped_lines_cannot_become_names_after_namespace_normalization(prediction):
    gold = {"functions": ["expected_tool"], "subset": "simple"}
    # Preserve the whole invalid line: neither dots in arguments nor a trailing
    # dotted identifier may hide the wrong call and produce a name-only match.
    assert dataset.parse_prediction(prediction) == [prediction]
    assert dataset.bare_name(prediction) == prediction
    verdict = dataset.score(prediction, gold)
    assert verdict["reward"] == 0.0
    assert verdict["official"]["predicted_functions"] == [prediction]
    assert dataset.score(prediction, {"functions": [], "subset": "irrelevance"})[
        "reward"] == 0.0


@pytest.mark.parametrize("prediction", ["expected_tool", "namespace.expected_tool",
                                         "outer.namespace.expected_tool"])
def test_valid_names_remain_namespace_normalized(prediction):
    assert dataset.bare_name(prediction) == "expected_tool"
    assert dataset.score(prediction, {"functions": ["expected_tool"]})["reward"] == 1.0
