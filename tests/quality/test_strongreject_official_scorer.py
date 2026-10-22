"""Differentials against the verbatim official function; these are not model runs."""
import ast
import hashlib
import importlib.util
from pathlib import Path

import pytest
from data.datasets import strongreject as dataset


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCORING = load_module(dataset.ASSETS / "lib/scoring.py", "strongreject_scoring_test")
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/strongreject_rubric.py"


def official(text):
    module = load_module(FIXTURE, "strongreject_official_test")
    module.JUDGE_TEMPLATES = {"strongreject_rubric": "{forbidden_prompt}/{response}", "strongreject_rubric_system": "judge"}
    module._generate_judge_response = lambda prompt, parser, **kwargs: parser(text)
    return module.strongreject_rubric("fixture request", "fixture response")


@pytest.mark.parametrize("refusal", [0, 1])
@pytest.mark.parametrize("convincingness", range(1, 6))
@pytest.mark.parametrize("specificity", range(1, 6))
def test_all_valid_rubric_combinations(refusal, convincingness, specificity):
    text = f"1.b {refusal}\n2.b {convincingness}\n3.b {specificity}"
    assert SCORING.score_rubric(text) == official(text)


@pytest.mark.parametrize("text", ["", "1.b  1\n2.b 5\n3.b 5", "1.b 1\n2.b 5"])
def test_preserves_official_parse_failures(text):
    with pytest.raises(IndexError):
        official(text)
    with pytest.raises(IndexError):
        SCORING.score_rubric(text)


@pytest.mark.parametrize("text", ["1.b 0\n2.b 9\n3.b 9", "1.b 9\n2.b 1\n3.b 1"])
def test_invalid_items_never_become_clipped_valid_scores(text):
    assert isinstance(official(text)["score"], float)
    with pytest.raises(ValueError, match="range"):
        SCORING.score_rubric(text)


def test_verbatim_function_has_not_drifted():
    entry = next(e for e in dataset.SOURCES["files"] if e["path"] == "upstream-evaluate.py")
    if not (dataset.DATASET_ROOT / entry["path"]).exists():
        pytest.skip("prepare StrongREJECT pinned sources for source differential")
    source = dataset.checked_bytes(entry).decode()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "strongreject_rubric")
    fixture = next(n for n in ast.parse(FIXTURE.read_text()).body if isinstance(n, ast.FunctionDef))
    node.decorator_list = []
    assert ast.dump(node) == ast.dump(fixture)
    assert node.lineno == 378


def test_templates_are_official_and_pinned():
    if not dataset.provision_status()["present"]:
        pytest.skip("prepare StrongREJECT source data")
    entry = next(e for e in dataset.SOURCES["files"] if e["path"] == "upstream-judge_templates.json")
    assert hashlib.sha256(dataset.checked_bytes(entry)).hexdigest() == "6d01841899e99f1f57c4f19153056051684ede2a7e1b54ebbb37b5ddff981768"
    spec = dataset.VERIFY[next(iter(dataset.rows()))]
    assert spec["judge"] == dataset.JUDGE
    assert spec["judge"]["model"] == "gpt-4o-mini-2024-07-18"
    assert spec["judge"]["max_calls"] == 1
