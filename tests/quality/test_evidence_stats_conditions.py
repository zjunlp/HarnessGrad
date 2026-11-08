"""Comparison requires recorded, compatible protocol and isolation provenance."""
import copy

import pytest

from harnessgrad import evidence_stats, verification_context


@pytest.fixture
def states(monkeypatch):
    monkeypatch.setattr(evidence_stats, "complete", lambda point: True)
    run = {"meta": {"dataset": "synthetic", "platform": {"content_sha": "p"},
                    "config": {"agent": {}, "environment": {}, "judge": {}, "sandbox": {}}}}
    point = {"identity": {"agent_model": "m", "agent_backend": "b", "harness_name": "h",
                          "framework_version": "v", "trials": 1, "jobs": 1},
             "side": "eval", "score_kind": "exam", "scoring_identity": {"sha256": "s"},
             "verification_context": verification_context.create({'a': '1' * 64, 'b': '2' * 64}),
             "env": {}, "sampling": {"policy": "all"}, "per_task": {"a": 1, "b": 0},
             "evaluation_protocols": {}, "scorer_isolation_class": "separate-pristine",
             "per_task_scorer_isolation": {"a": "separate-pristine", "b": "separate-pristine"}}
    return run, point


def comparison(states, mutate):
    run, point = states
    other = copy.deepcopy(point)
    mutate(other)
    return evidence_stats.compare_conditions(run, point, run, other)


def test_explicit_empty_protocol_is_comparable(states):
    assert comparison(states, lambda p: None)["comparable"]


@pytest.mark.parametrize("mutate,field", [
    (lambda p: p.pop("evaluation_protocols"), "evaluation_protocols"),
    (lambda p: p.update(evaluation_protocols={"a": "one"}), "evaluation_protocols"),
    (lambda p: p.update(evaluation_protocols={"a": "one", "b": "unknown"}), "evaluation_protocols"),
    (lambda p: p.pop("scorer_isolation_class"), "scorer_isolation_class"),
    (lambda p: p.update(scorer_isolation_class="unknown"), "scorer_isolation_class"),
    (lambda p: p.pop("per_task_scorer_isolation"), "per_task_scorer_isolation"),
    (lambda p: p["per_task_scorer_isolation"].pop("a"), "per_task_scorer_isolation"),
    (lambda p: p["per_task_scorer_isolation"].update(a="unknown"), "per_task_scorer_isolation"),
])
def test_missing_or_incomplete_conditions_are_unknown(states, mutate, field):
    result = comparison(states, mutate)
    assert not result["comparable"]
    assert field in result["missing_conditions"]


def test_different_protocols_are_incompatible(states):
    states[1]["evaluation_protocols"] = {"a": "one", "b": "one"}
    result = comparison(states, lambda p: p["evaluation_protocols"].update(a="two"))
    assert not result["comparable"]
    assert "evaluation_protocols" in result["changed_conditions"]


def test_different_isolation_is_incompatible(states):
    def change(p):
        p["scorer_isolation_class"] = "in-process"
        p["per_task_scorer_isolation"] = {"a": "in-process", "b": "in-process"}
    result = comparison(states, change)
    assert not result["comparable"]
    assert "scorer_isolation_class" in result["changed_conditions"]
    assert "per_task_scorer_isolation" in result["changed_conditions"]


def test_between_run_variance_rejects_unknown_protocol(states):
    run, point = states
    left, right = copy.deepcopy(run), copy.deepcopy(run)
    left["run_id"], right["run_id"] = "left", "right"
    point.update(measured_by_platform=True, score=0.5)
    point["identity"]["harness_sha"] = "same-tree"
    other = copy.deepcopy(point)
    other.pop("evaluation_protocols")
    result = evidence_stats.run_variation([(left, point), (right, other)])
    assert result["status"] == "unavailable"
    assert result["sample_variance"] is None
    assert "evaluation_protocols" in result["missing_conditions"]
