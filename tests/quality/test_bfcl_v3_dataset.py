"""BFCL-v3's contract: the official data shape, the name-only metric, and its limits.

Three things carry the weight here.

* `test_the_data_is_the_official_tag_not_the_hugging_face_copy` pins **where the data comes
  from**. Measured: the HuggingFace dataset's `BFCL_v3_simple.json` at its current revision is
  answer-only (`{"id", "ground_truth"}`) and byte-identical to its `possible_answer/` copy, so
  a loader pinned to it would grade itself. The tag `v1.3` has both files, and this test asserts
  the fields that only the real question file has.
* `test_only_names_are_scored_and_the_record_says_so` pins the metric's **limit**: the official
  BFCL score needs argument values, and the answer file gives them as free-form acceptable-value
  lists, so no scorer can enumerate them. A record that reported a name-only number *as* the
  official score would be the §1 error.
* `test_irrelevance_gold_is_the_empty_set` pins the subset that tests the decision rather than
  the arguments -- calling anything at all there is a miss.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def bfcl():
    import data.datasets.bfcl_v3 as module
    if not module._RECORDS:
        pytest.skip(f"no BFCL data under {module.DATASET_ROOT}; run "
                    f"`python3 tools/datasets.py --fetch --dataset bfcl_v3`")
    return module


# ------------------------------------------------------------- the data source ---


def test_the_data_is_the_official_tag_not_the_hugging_face_copy(bfcl):
    """The question files carry `question` **and** `function`; the answer file carries neither.

    This is the measurement that decided the pin. Asserting it here means a future
    "simplification" to the HF dataset fails loudly rather than loading an answer file and
    scoring every task against itself.
    """
    simple = bfcl.DATASET_ROOT / bfcl.SIMPLE
    answers = bfcl.DATASET_ROOT / bfcl.SIMPLE_ANSWERS
    question_rows = [json.loads(line) for line in simple.read_text().splitlines() if line.strip()]
    answer_rows = [json.loads(line) for line in answers.read_text().splitlines() if line.strip()]
    assert len(question_rows) == bfcl.N_SIMPLE
    assert len(answer_rows) == bfcl.N_SIMPLE
    assert {"id", "question", "function"} <= set(question_rows[0]), sorted(question_rows[0])
    assert {"id", "ground_truth"} <= set(answer_rows[0])
    assert "question" not in answer_rows[0], (
        "the answer file must not carry the question; if it does, the two files are the same "
        "file and the gold is not independent of the task")
    assert simple.read_bytes() != answers.read_bytes(), (
        "the question and answer files must differ")


def test_the_tag_and_the_download_are_both_pinned(bfcl):
    entry = next(e for e in bfcl.downloads() if e["kind"] == "http")
    assert entry["version"] == "v1.3", "the tag, not a branch"
    assert len(entry["sha256"]) == 64
    assert entry["url"].endswith("refs/tags/v1.3")


def test_irrelevance_needs_no_answer_file(bfcl):
    """Its gold is the empty set, which is why it is the cheapest labelled decision here."""
    rows = [r for r in bfcl._RECORDS if r["subset"] == "irrelevance"]
    assert len(rows) == bfcl.N_IRRELEVANCE
    assert all(r["gold"]["functions"] == [] for r in rows)
    assert all(r["gold"]["answer"] == "NONE" for r in rows)


# ------------------------------------------------------------------ the metric ---


def test_only_names_are_scored_and_the_record_says_so(bfcl):
    """The grade is set equality over names, and the evidence says "not the official score"."""
    record = bfcl._RECORDS[0]
    result = bfcl.score(record["gold"]["answer"], record["gold"])
    assert result["reward"] == 1.0
    official = result["official"]
    assert "not the official BFCL score" in official["metric"]
    assert "arguments" in official["official_metric"]
    # A wrong name is a miss, case-sensitively: function names are identifiers.
    assert bfcl.score("something_else", record["gold"])["reward"] == 0.0


def test_the_argument_values_are_free_form_lists(bfcl):
    """Why the official metric is unreachable: measured on the answer file.

    Each argument maps to a list of acceptable values (`unit: ["units", ""]`), so "the correct
    call" is a set of alternatives with no enumerable candidate set.
    """
    answers = [json.loads(line) for line in
               (bfcl.DATASET_ROOT / bfcl.SIMPLE_ANSWERS).read_text().splitlines() if line.strip()]
    free_form = 0
    for row in answers:
        for call in row["ground_truth"]:
            for _fn, args in call.items():
                for _arg, acceptable in args.items():
                    assert isinstance(acceptable, list), (_arg, acceptable)
                    if any(not isinstance(v, (int, float)) for v in acceptable):
                        free_form += 1
    assert free_form > 0, "if every argument were enumerable, the official metric would be in reach"


def test_a_partial_answer_is_a_miss(bfcl):
    """Set equality, not recall: naming one of two needed functions scores 0."""
    gold = {"functions": ["alpha", "beta"], "subset": "simple"}
    assert bfcl.score("alpha\nbeta", gold)["reward"] == 1.0
    assert bfcl.score("beta\nalpha", gold)["reward"] == 1.0, "order does not matter"
    assert bfcl.score("alpha", gold)["reward"] == 0.0
    assert bfcl.score("alpha\nbeta\ngamma", gold)["reward"] == 0.0


def test_none_is_a_way_to_say_no_tool(bfcl):
    """`NONE` and an empty file both mean the empty set, and both are spelled in the goal."""
    empty_gold = {"functions": [], "subset": "irrelevance"}
    assert bfcl.score("NONE", empty_gold)["reward"] == 1.0
    assert bfcl.score("none", empty_gold)["reward"] == 1.0
    assert bfcl.score("No tool", empty_gold)["reward"] == 1.0
    assert bfcl.score("", empty_gold)["reward"] == 1.0
    assert bfcl.score("calculate_triangle_area", empty_gold)["reward"] == 0.0


# ------------------------------------------------------------------ the schemas ---


def test_bfcl_s_parameter_types_are_normalised_to_json_schema(bfcl):
    """`dict`/`float`/`tuple` are BFCL's words; a model reading a schema should get JSON Schema."""
    fixed = bfcl.normalize_schema({"type": "dict", "properties": {
        "a": {"type": "float"}, "b": {"type": "tuple"}, "c": {"type": "string"}}})
    assert fixed["type"] == "object"
    assert fixed["properties"]["a"]["type"] == "number"
    assert fixed["properties"]["b"]["type"] == "array"
    assert fixed["properties"]["c"]["type"] == "string", "valid types pass through"


def test_every_rendered_schema_is_json(bfcl):
    """A goal that is not valid JSON is worse than no schema: the agent cannot parse it."""
    for record in bfcl._RECORDS[:50]:
        parsed = json.loads(record["functions_json"])
        assert isinstance(parsed, list) and parsed
        for fn in parsed:
            assert fn["name"] and isinstance(fn["parameters"], dict)


def test_each_question_carries_its_own_functions(bfcl):
    """BFCL gives each question its own tool set, so a global toolbox would be wrong."""
    counts = {len(r["function_names"]) for r in bfcl._RECORDS}
    assert counts, "the records carry function lists"
    # Every record's gold names must be a subset of that record's own functions, compared on
    # bare names: measured, `simple_363` has gold `find_closest` against a schema of
    # `restaurant_search.find_closest`. `bare_name` is what makes the comparison meaningful,
    # and the one row that needs it is pinned below.
    for record in bfcl._RECORDS:
        assert set(record["gold"]["functions"]) <= {bfcl.bare_name(n)
                                                   for n in record["function_names"]}, \
            record["task_id"]


def test_the_one_namespaced_gold_row_is_handled(bfcl):
    """`simple_363`: gold `find_closest`, schema `restaurant_search.find_closest`.

    A scorer comparing the strings directly would mark a correct answer wrong on this row, and
    the row count is small enough that the error would look like a model failure.
    """
    gold = {"functions": ["find_closest"], "subset": "simple"}
    assert bfcl.score("find_closest", gold)["reward"] == 1.0
    assert bfcl.score("restaurant_search.find_closest", gold)["reward"] == 1.0, \
        "the namespaced spelling the goal showed is also correct"
    assert bfcl.score("find_closest_other", gold)["reward"] == 0.0
