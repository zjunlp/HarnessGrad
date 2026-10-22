"""`tools/method_lint.py`: the gate that would have caught a `method.json` nobody could read.

The failure this exists for is measured, not hypothetical.
`methods/dream_rsi/method.json` shipped with unescaped quotes inside two of its string values,
so it was not valid JSON; `tools/serve_ui.py` swallowed the `JSONDecodeError` into `meta = {}`,
and the method appeared in the chooser as `kind: "other"`, titled by its directory name, with an
empty note. No test read the file, so nothing objected.

Two directions, both required. The first half runs the lint over the real `methods/` so a port
cannot quietly lose its declaration or its rule test. The second half feeds `lint_one` synthetic
directories that must **fail**, so a checker that silently stopped checking would fail here
rather than pass for years.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import method_lint  # noqa: E402

#: A name no real test file can match, so the probe's verdict comes from the check under
#: test rather than from whatever the test tree happens to contain.
PROBE = "probe_method_zzz"


def _probe(tmp_path: Path, *, meta=None, run: str = "print('hi')\n") -> Path:
    directory = tmp_path / PROBE
    directory.mkdir()
    (directory / "run.py").write_text(run)
    if meta is not None:
        (directory / "method.json").write_text(
            meta if isinstance(meta, str) else json.dumps(meta))
    return directory


def _minimal(kind: str = "reference", **extra) -> dict:
    return {"kind": kind, "title": "t", "note": "n", "deviations": [], **extra}


# ------------------------------------------------------- the real catalogue ---


def test_every_method_passes_the_lint():
    failures = [r for r in method_lint.lint_all() if not r.ok]
    assert not failures, "\n".join(f"{r.name}: {'; '.join(r.errors)}" for r in failures)


def test_every_published_port_declares_a_deviation_and_ships_a_rule_test():
    """The two things a baseline needs before its number can go in a table.

    A port without a declared deviation cannot be compared fairly against the published
    number; a port without a rule test is prose standing behind 200-1,100 lines of code.
    """
    published = [r for r in method_lint.lint_all() if r.kind == "published"]
    assert len(published) >= 9, [r.name for r in published]
    for result in published:
        assert result.deviations >= 1, result.name
        assert result.rule_test, result.name


def test_both_trajectory_shapes_declare_themselves():
    """`methods/a` and `methods/b` are the platform's own shapes, not ports.

    They had no `method.json` at all, which is how the chooser showed them as `kind: "other"`.
    """
    by_name = {r.name: r for r in method_lint.lint_all()}
    for name in ("a", "b"):
        assert by_name[name].kind == "reference", name
        assert by_name[name].ok, by_name[name].errors


def test_the_lint_names_the_catalogue_the_chooser_uses():
    names = {r.name for r in method_lint.lint_all()}
    assert {"noop", "echo_base", "llm_improver", "ahe", "rrsi", "meta_harness"} <= names


# ------------------------------------------------------------------- teeth ---


def test_a_method_json_that_does_not_parse_is_an_error(tmp_path):
    """The exact shape that shipped: ASCII quotes inside a Chinese string value."""
    broken = ('{\n "kind": "published",\n "title": "Dream-RSI:把"部署哪个版本"变成 argmax V",\n'
              ' "note": "x"\n}\n')
    result = method_lint.lint_one(_probe(tmp_path, meta=broken))
    assert any("not valid JSON" in error for error in result.errors), result.errors


def test_a_missing_method_json_is_an_error(tmp_path):
    result = method_lint.lint_one(_probe(tmp_path))
    assert any("no method.json" in error for error in result.errors), result.errors


def test_a_published_port_with_no_declared_deviation_is_an_error(tmp_path):
    result = method_lint.lint_one(_probe(tmp_path, meta=_minimal("published")))
    assert any("deviates" in error for error in result.errors), result.errors


def test_a_published_port_with_no_rule_test_is_an_error(tmp_path):
    meta = _minimal("published",
                    deviations=[{"what": "w", "why": "y", "direction": "weaker"}])
    result = method_lint.lint_one(_probe(tmp_path, meta=meta))
    assert any("no rule test" in error for error in result.errors), result.errors


def test_a_deviation_direction_outside_the_vocabulary_is_an_error(tmp_path):
    meta = _minimal("reference", deviations=[{"what": "w", "why": "y",
                                              "direction": "probably fine"}])
    result = method_lint.lint_one(_probe(tmp_path, meta=meta))
    assert any("direction" in error for error in result.errors), result.errors


def test_a_deviation_without_a_reason_is_an_error(tmp_path):
    meta = _minimal("reference", deviations=[{"what": "w", "direction": "weaker"}])
    result = method_lint.lint_one(_probe(tmp_path, meta=meta))
    assert any("why is empty" in error for error in result.errors), result.errors


def test_a_method_with_its_own_model_client_is_an_error(tmp_path):
    result = method_lint.lint_one(_probe(
        tmp_path, meta=_minimal("reference"),
        run="from openai import OpenAI\nclient = OpenAI()\n"))
    assert any("model client" in error for error in result.errors), result.errors


def test_model_names_in_metadata_strings_are_not_client_construction(tmp_path):
    result = method_lint.lint_one(_probe(
        tmp_path, meta=_minimal("reference"),
        run='MODEL = "anthropic/claude-sonnet-4-5"\n'))
    assert not [e for e in result.errors if "model client" in e], result.errors


def test_a_rule_test_is_named_after_the_method_not_after_a_later_token(
        tmp_path, monkeypatch):
    """A decoy ending in the method's letter is not this method's rule test.

    Measured: the loose token search matched `test_scorer_isolation_option_b.py` -- "option B"
    of scorer isolation -- for the method named `b`, and demanded a citation for a rule that
    file does not implement. Both halves are asserted here, because a fix that simply stopped
    finding rule tests would also silence the citation requirement.
    """
    tests = tmp_path / "tests"
    tests.mkdir()
    monkeypatch.setattr(method_lint, "TESTS", tests)
    (tests / "test_unrelated_option_probe_method_zzz.py").write_text("no citation here\n")
    (tmp_path / "decoy").mkdir()
    decoy = method_lint.lint_one(_probe(tmp_path / "decoy", meta=_minimal("reference")))
    assert decoy.rule_test is None, decoy.rule_test
    assert not decoy.errors, decoy.errors

    (tests / "test_probe_method_zzz_rule.py").write_text("still no citation\n")
    (tmp_path / "named").mkdir()
    named = method_lint.lint_one(_probe(tmp_path / "named", meta=_minimal("reference")))
    assert named.rule_test == "test_probe_method_zzz_rule.py", named.rule_test
    assert any("does not cite the rule" in error for error in named.errors), named.errors


@pytest.mark.parametrize("run", [
    "import anthropic\nclient = anthropic.Anthropic()\n",
    "from anthropic import Anthropic as Client\nclient = Client()\n",
    "import requests as http\nhttp.post('https://api.example')\n",
    "import litellm as llm\nllm.completion(model='x')\n",
    "client.chat.completions.create(model='x')\n",
])
def test_actual_model_client_construction_or_api_calls_are_errors(tmp_path, run):
    result = method_lint.lint_one(_probe(tmp_path, meta=_minimal("reference"), run=run))
    assert any("model client" in error for error in result.errors), result.errors


def test_a_port_that_never_reaches_the_editor_is_an_error(tmp_path):
    meta = _minimal("published",
                    deviations=[{"what": "w", "why": "y", "direction": "weaker"}])
    # The probe body must not contain the word the check looks for: the first version of
    # this test said `print('no editor here')` and passed for that reason.
    result = method_lint.lint_one(_probe(tmp_path, meta=meta, run="print('nothing shared')\n"))
    assert any("editor" in error for error in result.errors), result.errors


def test_a_reference_method_may_skip_the_editor(tmp_path):
    """`noop` and `echo_base` are the platform's own; they do not propose through it."""
    result = method_lint.lint_one(_probe(tmp_path, meta=_minimal("reference"),
                                         run="print('nothing shared')\n"))
    assert not [e for e in result.errors if "editor" in e], result.errors


def test_an_empty_title_is_an_error(tmp_path):
    result = method_lint.lint_one(_probe(tmp_path, meta={"kind": "reference", "title": " ",
                                                         "note": "n", "deviations": []}))
    assert any("title is empty" in error for error in result.errors), result.errors


def test_the_cli_exits_non_zero_when_something_fails(tmp_path, monkeypatch):
    """The exit code is the interface: `tools/method_lint.py` is meant to be run in a gate."""
    monkeypatch.setattr(method_lint, "METHODS", tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "run.py").write_text("print('hi')\n")
    assert method_lint.main([]) == 1
