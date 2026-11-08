"""Authored BFCL JSONL loader, name-only proxy and schema controls.

Default tests use synthetic files, not released BFCL data. The tag/download
pin is inspected independently of payload identity. HG_TEST_BFCL_DATA_ROOT
explicitly enables the existing-release pool check; no test downloads it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def bfcl(tmp_path):
    """Authored JSONL through the real loader; no released BFCL payload."""
    from stdlib_authored_fixture_helpers import adapter_at
    module = adapter_at('bfcl_v3', tmp_path)
    module.N_SIMPLE, module.N_IRRELEVANCE = 2, 1
    def write(relative, rows):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    def row(identifier, function):
        return {'id': identifier, 'question': [[{'role':'user','content':'Authored request.'}]],
                'function': [{'name':function, 'description':'Authored function.',
                              'parameters':{'type':'dict','properties':{'x':{'type':'float'}}}}]}
    write(module.SIMPLE, [row('authored-0','lookup'),
                          row('authored-1','restaurant_search.find_closest')])
    write(module.SIMPLE_ANSWERS, [
        {'id':'authored-0','ground_truth':[{'lookup':{'x':['fictional value','']}}]},
        {'id':'authored-1','ground_truth':[{'find_closest':{'x':[1,2]}}]}])
    write(module.IRRELEVANCE, [row('authored-irrelevant','unneeded')])
    tasks, _ = module.load()
    assert len(tasks) == 3
    return module


@pytest.fixture
def released_bfcl():
    """Explicit, read-only integration source; never choose HG_DATA_ROOT implicitly."""
    import os
    from stdlib_authored_fixture_helpers import adapter_at
    selected = os.environ.get('HG_TEST_BFCL_DATA_ROOT')
    if not selected:
        pytest.skip('released BFCL opt-in: set HG_TEST_BFCL_DATA_ROOT to an existing pinned data root')
    root = Path(selected).expanduser()
    module = adapter_at('bfcl_v3', root)
    missing = [str(root / item) for item in (module.SIMPLE, module.SIMPLE_ANSWERS, module.IRRELEVANCE)
               if not (root / item).is_file()]
    if missing:
        pytest.skip('released BFCL opt-in missing files: ' + ', '.join(missing))
    module.load()  # Present but invalid data must fail, not become a skip.
    return module


def test_released_pool_shape_when_explicitly_selected(released_bfcl):
    module = released_bfcl
    assert len(module._RECORDS) == module.N_SIMPLE + module.N_IRRELEVANCE
    assert all(r['user_request'] and r['function_names'] for r in module._RECORDS)


# ------------------------------------------------------------- the data source ---


def test_loader_keeps_question_and_answer_files_independent(bfcl):
    """Authored question files carry `question` and `function`; answers carry neither.

    This authored shape exercises the same boundary as the historical HF answer-file
    confusion. Release provenance is checked separately through the explicit root;
    generating these bytes cannot establish the identity of an upstream dataset.
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


def test_loader_refuses_answer_rows_used_as_questions(bfcl):
    """Exercise the historical HF question/answer confusion through the loader."""
    (bfcl.DATASET_ROOT / bfcl.SIMPLE).write_bytes(
        (bfcl.DATASET_ROOT / bfcl.SIMPLE_ANSWERS).read_bytes())
    bfcl._RECORDS = []
    with pytest.raises(ValueError, match='no user message'):
        bfcl.load()


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
    """The name-only proxy keeps argument alternatives in the authored source file.

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
    """Authored namespaced row mirrors the historical `simple_363` shape.

    A scorer comparing the strings directly would mark a correct answer wrong on this row, and
    the row count is small enough that the error would look like a model failure.
    """
    gold = {"functions": ["find_closest"], "subset": "simple"}
    assert bfcl.score("find_closest", gold)["reward"] == 1.0
    assert bfcl.score("restaurant_search.find_closest", gold)["reward"] == 1.0, \
        "the namespaced spelling the goal showed is also correct"
    assert bfcl.score("find_closest_other", gold)["reward"] == 0.0
