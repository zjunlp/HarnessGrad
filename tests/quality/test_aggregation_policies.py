"""Fail-closed, source-only inventory and synthetic aggregation regression gates.

Never call registry.published(): it imports adapters and can silently hide a broken
one. The explicitly out-of-scope adapter is required by name without opening it.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from data.scaffolds import aggregation
from data.scaffolds.qa import base
from eval.metrics import mean

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tests/fixtures/aggregation_s9_before.json"
SOURCE_EXCLUDED = {"agentdojo"}
FIXTURES = {"demo", "probe_set", "broken_service",
            "verify_demo", "verify_exec", "verify_service"}


def _published_sources(directory):
    """AST-read KIND even if an import would fail; never execute adapter source."""
    sources = {}
    for path in sorted(directory.glob("*.py")):
        if path.stem in SOURCE_EXCLUDED:
            sources[path.stem] = None
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        kinds = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if any(isinstance(t, ast.Name) and t.id == "KIND" for t in targets):
                kinds.append(ast.literal_eval(node.value))
        assert len(kinds) <= 1, f"{path.stem}: ambiguous KIND"
        if kinds:
            assert kinds[0] in {"dataset", "fixture"}, f"{path.stem}: invalid KIND"
        if kinds == ["dataset"]:
            sources[path.stem] = tree
    return sources


def _guard(directory, declarations):
    sources = _published_sources(directory)
    missing = set(sources) - set(declarations)
    assert not missing, f"missing aggregation policies: {sorted(missing)}"
    assert not (set(declarations) - set(sources) - FIXTURES), "stale aggregation policies"
    for name, tree in sources.items():
        scopes = {"run_reward"}
        if tree is not None and any(isinstance(n, ast.FunctionDef)
                                    and n.name in {"aggregate", "evaluate_predictions"}
                                    for n in tree.body):
            scopes.add("corpus_metrics")
        assert set(declarations[name]) == scopes, f"{name}: aggregation scopes differ"
        for scope in scopes:
            # The accessor validates the same manifest the guard inventories.
            assert aggregation.aggregation_policy(name, scope) == declarations[name][scope]
    return sources


def test_every_published_dataset_declares_aggregation_without_importing_it(monkeypatch):
    original_read = Path.read_text

    def scoped_read(path, *args, **kwargs):
        assert path.name != "agentdojo.py" and "agentdojo" not in path.parts
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", scoped_read)
    declarations = json.loads(aggregation.POLICIES.read_text())
    sources = _guard(ROOT / "data" / "datasets", declarations)
    assert SOURCE_EXCLUDED <= set(sources)


def test_new_unimportable_adapter_without_policy_fails_guard(tmp_path):
    (tmp_path / "new_dataset.py").write_text(
        'KIND: str = "dataset"\nraise RuntimeError("must never execute")\n')
    with pytest.raises(AssertionError, match="missing aggregation policies.*new_dataset"):
        _guard(tmp_path, {})


def test_dynamic_publication_declaration_fails_closed(tmp_path):
    (tmp_path / "dynamic.py").write_text('KIND = choose_kind()\n')
    with pytest.raises(ValueError):
        _published_sources(tmp_path)


def test_both_run_entrypoints_pass_dataset_to_reward_computation():
    for relative in ("driver.py", "harnessgrad/loops/eval_side.py"):
        tree = ast.parse((ROOT / relative).read_text())
        sampling_dicts = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)
                          and any(isinstance(k, ast.Constant) and k.value == "selected_within"
                                  for k in n.keys)]
        assert sampling_dicts, relative
        for node in sampling_dicts:
            values = {k.value: v for k, v in zip(node.keys, node.values)
                      if isinstance(k, ast.Constant)}
            assert ast.unparse(values["dataset"]) == "args.dataset", relative


@pytest.fixture
def adapters():
    # Block import-time pool construction before importing either scorer.
    with patch.object(base.QASpec, "records", return_value=[]):
        musique = importlib.import_module("data.datasets.musique")
        twowiki = importlib.import_module("data.datasets.twowikimultihopqa")
    with patch.object(musique, "_rows", side_effect=AssertionError("released data")), \
            patch.object(twowiki, "_rows", side_effect=AssertionError("released data")), \
            patch.object(twowiki, "_aliases", side_effect=AssertionError("released aliases")):
        yield musique, twowiki


def test_before_after_all_numbers_and_verdicts_are_identical(adapters):
    musique, twowiki = adapters
    baseline = json.loads(BASELINE.read_text())
    inputs, expected = baseline["inputs"], baseline["outputs"]
    gold, predictions = inputs["musique_gold"], inputs["musique_predictions"]
    results = [musique.score_prediction(p, gold) for p in predictions]
    actual = {
        "musique_items": results,
        "musique_complete": musique.aggregate(results),
        "musique_omitted_item": musique.aggregate(results[:1]),
        "musique_legacy": musique.aggregate([
            musique.score(p["answer"], gold, mode=musique.LEGACY_MODE) for p in predictions]),
        "twowiki_items": [twowiki.score_prediction(p, inputs["twowiki_golds"][0]) for p in (
            inputs["twowiki_predictions"]["complete"]["one"],
            {"schema_version": 1, "answer": "red"})],
        "twowiki_legacy": twowiki.score("red", inputs["twowiki_golds"][0]),
        "twowiki_corpus": {case: twowiki.evaluate_predictions(preds, inputs["twowiki_golds"])
                           for case, preds in inputs["twowiki_predictions"].items()},
        "run_means": {name: [mean(xs, dataset=name) for xs in inputs["run_scores"]]
                      for name in expected["run_means"]},
    }
    assert actual == expected
    # Keep the original migration receipt immutable. Later registrations have no
    # pre-migration result, but still participate in the same declared run mean.
    added_since_baseline = {"aime", "cooperbench", "costbench", "costbench_dynamic",
                            "kumo", "locomo", "tau3_bench"}
    assert set(actual["run_means"]) | added_since_baseline == set(
        _published_sources(ROOT / "data" / "datasets"))
    for name in added_since_baseline:
        assert [mean(xs, dataset=name) for xs in inputs["run_scores"]] == expected["run_means"]["gsm8k"]


def _replace_policy(monkeypatch, tmp_path, dataset, scope, **changes):
    declarations = json.loads(aggregation.POLICIES.read_text())
    declarations[dataset][scope].update(changes)
    target = tmp_path / "aggregation_policies.json"
    target.write_text(json.dumps(declarations))
    monkeypatch.setattr(aggregation, "POLICIES", target)


@pytest.mark.parametrize("dataset", sorted(json.loads(BASELINE.read_text())["outputs"]["run_means"]))
def test_every_published_run_policy_is_live(dataset, monkeypatch, tmp_path):
    # A valid alternate rule needs a count the run does not supply. If reward code
    # ignores the declaration this will return a number and the test will fail.
    _replace_policy(monkeypatch, tmp_path, dataset, "run_reward", denominator="gold_rows")
    with pytest.raises(ValueError, match="gold_rows needs"):
        mean([1.0, 0.0], dataset=dataset)
    with pytest.raises(ValueError, match="gold_rows needs"):
        mean([], dataset=dataset)


@pytest.mark.parametrize("dataset", ["musique", "twowikimultihopqa"])
def test_native_corpus_declarations_are_live(dataset, adapters, monkeypatch, tmp_path):
    musique, twowiki = adapters
    _replace_policy(monkeypatch, tmp_path, dataset, "corpus_metrics", denominator="measured_tasks")
    inputs = json.loads(BASELINE.read_text())["inputs"]
    with pytest.raises(ValueError, match="measured_tasks needs"):
        if dataset == "musique":
            musique.aggregate([musique.score_prediction(
                inputs["musique_predictions"][0], inputs["musique_gold"])])
        else:
            twowiki.evaluate_predictions(inputs["twowiki_predictions"]["missing_row"],
                                         inputs["twowiki_golds"])


@pytest.fixture
def scienceworld_measurements():
    from data.datasets import scienceworld

    # Authored scores, never benchmark executions. Unequal variation counts expose
    # the difference between a micro mean and equal weight per task type.
    records = scienceworld.test_fold()
    first_type = records[0]["task_name"]
    first = [r["task_id"] for r in records if r["task_name"] == first_type][:3]
    other = next(r["task_id"] for r in records if r["task_name"] != first_type)
    return scienceworld, {**dict.fromkeys(first, 0.0), other: 1.0}


def test_scienceworld_reports_both_means_and_observed_coverage(scienceworld_measurements):
    scienceworld, measured = scienceworld_measurements
    report = scienceworld.aggregate(measured, step_budget=100)
    assert report["micro_mean"] == mean(list(measured.values()), dataset="scienceworld") == 0.25
    assert report["macro_over_observed_task_types"] == 0.5
    assert report["macro_denominator"] == report["task_types_observed"] == 2
    assert report["n_measured"] == 4
    assert report["n_eligible"] == 1744
    assert report["n_unmeasured"] == 1740
    assert sorted(row["n_measured"] for row in report["per_task_type"].values()) == [1, 3]
    assert report["task_types_released"] == 29
    assert report["task_types_paper"] == 30
    assert len(report["unobserved_released_task_types"]) == 27
    assert report["missing_released_task_type"] == "measure-melting-point-unknown-substance"
    assert report["paper_30_type_average"] is None
    assert "29/30" in report["comparability"]
    assert report["step_budget"] == 100
    assert "truncate at 100" in report["limit"]
    assert report["configured_default_step_budget"] == 100
    assert report == scienceworld.aggregate(dict(reversed(list(measured.items()))), step_budget=100)


def test_scienceworld_complete_released_fold_is_still_29_of_30(scienceworld_measurements):
    scienceworld, _ = scienceworld_measurements
    report = scienceworld.aggregate({r["task_id"]: 1.0 for r in scienceworld.test_fold()})
    assert report["micro_mean"] == report["macro_over_observed_task_types"] == 1.0
    assert report["task_types_observed"] == report["macro_denominator"] == 29
    assert report["n_unmeasured"] == 0
    assert report["unobserved_released_task_types"] == []
    assert report["paper_30_type_average"] is None


def test_scienceworld_empty_measurements_do_not_invent_zeroes(scienceworld_measurements):
    scienceworld, _ = scienceworld_measurements
    report = scienceworld.aggregate({})
    assert report["micro_mean"] is report["macro_over_observed_task_types"] is None
    assert report["macro_denominator"] == report["task_types_observed"] == 0
    assert report["n_unmeasured"] == 1744


def test_scienceworld_historical_budget_is_never_inferred_from_current_default(scienceworld_measurements):
    scienceworld, measured = scienceworld_measurements
    assert scienceworld.aggregate(measured)["step_budget"] is None
    report = scienceworld.aggregate(measured, step_budget=1000)
    assert report["step_budget"] == 1000
    assert report["configured_default_step_budget"] == 100


@pytest.mark.parametrize("budget", [0, -1, True, 100.0])
def test_scienceworld_source_budget_requires_an_explicit_positive_integer(
        scienceworld_measurements, budget):
    scienceworld, measured = scienceworld_measurements
    with pytest.raises(ValueError, match="source step_budget"):
        scienceworld.aggregate(measured, step_budget=budget)


@pytest.mark.parametrize("reward", [None, float("nan"), float("inf"), -0.1, 1.1, True, "0.5"])
def test_scienceworld_invalid_rewards_are_not_zeroes(scienceworld_measurements, reward):
    scienceworld, measured = scienceworld_measurements
    with pytest.raises(ValueError, match="invalid measured reward"):
        scienceworld.aggregate({next(iter(measured)): reward})


def test_scienceworld_unknown_task_cannot_create_an_extra_type(scienceworld_measurements):
    scienceworld, _ = scienceworld_measurements
    with pytest.raises(ValueError, match="unknown test-fold task"):
        scienceworld.aggregate({"measure-melting-point-unknown-substance/0": 0.0})


@pytest.mark.parametrize("change, message", [
    ({"denominator": "measured_tasks"}, "measured_tasks needs"),
    ({"aggregation": "arithmetic_mean"}, "requires ordered_macro_mean"),
])
def test_scienceworld_corpus_policy_is_live(
        scienceworld_measurements, monkeypatch, tmp_path, change, message):
    scienceworld, measured = scienceworld_measurements
    _replace_policy(monkeypatch, tmp_path, "scienceworld", "corpus_metrics", **change)
    with pytest.raises(ValueError, match=message):
        scienceworld.aggregate(measured)


def test_scienceworld_report_cli_exports_both_means_without_environment(
        scienceworld_measurements, tmp_path):
    import subprocess
    import sys

    scienceworld, measured = scienceworld_measurements
    point = tmp_path / "synthetic-point.json"
    point.write_text(json.dumps({"per_task": measured}))
    result = subprocess.run([sys.executable, "-m", "data.datasets.scienceworld",
                             "--aggregate", str(point), "--step-budget", "100"], cwd=ROOT,
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == scienceworld.aggregate(measured, step_budget=100)


def test_recorded_reward_uses_the_policy_and_keeps_unmeasured_tasks_out(monkeypatch, tmp_path):
    from harnessgrad.records import _curve_point

    arguments = dict(run_id="synthetic", round_index=0, identity={},
                     sampling={"dataset": "musique"}, scores=[1.0], task_ids=["measured"],
                     # Model the observed command route; unknown cannot be scored.
                     per_task_scorer_isolation={"measured": "actor-workdir"},
                     invalid={"unmeasured": {"reason": "synthetic scorer failure"}},
                     train_scores=[0.0, 1.0], side="eval", cost={}, cumulative={})
    point = _curve_point(**arguments)
    assert point["score"] == 1.0
    assert point["train_score"] == 0.5
    assert point["n_invalid"] == point["n_scored"] == 1
    _replace_policy(monkeypatch, tmp_path, "musique", "run_reward", denominator="gold_rows")
    with pytest.raises(ValueError, match="gold_rows needs"):
        _curve_point(**arguments)


@pytest.mark.parametrize("policy", [
    {}, {"aggregation": "sum", "denominator": "gold_rows"},
    {"aggregation": "arithmetic_mean", "denominator": "guessed"},
    {"aggregation": "arithmetic_mean", "denominator": "measured_tasks", "extra": True},
])
def test_malformed_policy_fails_closed(policy, monkeypatch, tmp_path):
    target = tmp_path / "aggregation_policies.json"
    target.write_text(json.dumps({"new_dataset": {"run_reward": policy}}))
    monkeypatch.setattr(aggregation, "POLICIES", target)
    with pytest.raises(ValueError, match="invalid aggregation policy"):
        mean([1.0], dataset="new_dataset")


def test_undeclared_runtime_adapter_fails_closed():
    with pytest.raises(ValueError, match="missing aggregation policy"):
        mean([1.0], dataset="new_dataset_without_declaration")
