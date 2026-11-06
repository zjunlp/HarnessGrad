"""Descriptive variation from recorded observations, never from a task CI.

Per-task trials are not independent optimization runs. Between-run estimates are
limited to distinct, complete measurements of the same harness and conditions.
Recorded provenance supports comparability, not proof of statistical independence.
"""
from __future__ import annotations

import math
import statistics

from harnessgrad.evidence import finite_number
from harnessgrad.receipt_verification import measurement_problems


def unavailable(reason):
    return {"status": "unavailable", "reason": reason, "sample_variance": None}


def _at(value, path):
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def reused(point):
    return (point.get("reused_from") is not None or bool(point.get("shared_from"))
            or bool(point.get("cached_tasks")) or bool(point.get("n_cached"))
            or point.get("measured_by_platform") is not True)


def complete(point):
    if measurement_problems(point):
        return False
    selected = _at(point, "sampling.task_ids")
    return (isinstance(selected, list) and all(isinstance(t, str) for t in selected)
            and len(selected) == len(set(selected))
            and set(selected) == set(point["per_task"])
            and not any(point.get(k) for k in ("invalid", "unmeasured", "n_invalid", "n_unmeasured")))


def conditions(run, point):
    """Values used privately for comparisons; callers export only field names."""
    meta = run.get("meta") or {}
    values = {"identity." + name: _at(point, "identity." + name) for name in (
        "agent_model", "agent_backend", "harness_name", "framework_version", "trials", "jobs")}
    values.update({"dataset": meta.get("dataset"), "side": point.get("side"),
                   "score_kind": point.get("score_kind"),
                   "scoring_identity": point.get("scoring_identity"),
                   "environment": point.get("env"),
                   "platform": _at(meta, "platform.content_sha"),
                   "agent_config": _at(meta, "config.agent"),
                   "environment_config": _at(meta, "config.environment"),
                   "judge_config": _at(meta, "config.judge"),
                   "sandbox_config": _at(meta, "config.sandbox"),
                   "sampling_policy": _at(point, "sampling.policy"),
                   "task_set": sorted(point.get("per_task", {}))})
    tasks = set(point.get("per_task", {}))
    protocols = point.get("evaluation_protocols")
    if (not isinstance(protocols, dict) or
            (protocols and (set(protocols) != tasks or any(
                not isinstance(v, str) or not v or v == "unknown"
                for v in protocols.values())))):
        protocols = None
    isolation = point.get("scorer_isolation_class")
    task_isolation = point.get("per_task_scorer_isolation")
    if not isinstance(isolation, str) or not isolation or isolation == "unknown":
        isolation = None
    if (not isinstance(task_isolation, dict) or set(task_isolation) != tasks
            or not isolation or any(v != isolation for v in task_isolation.values())):
        task_isolation = None
    values.update({"evaluation_protocols": protocols,
                   "scorer_isolation_class": isolation,
                   "per_task_scorer_isolation": task_isolation})
    # The current metadata cannot identify the code used for every historical point
    # in a run resumed across platform versions.
    started = meta.get("platform_content_sha_started") or meta.get("platform_content_sha")
    if started and started != values["platform"]:
        values["platform"] = None
    if _at(point, 'identity.agent_model_conflict') or _at(point, 'identity.agent_backend_conflict'):
        values['identity.agent_model'] = None
    return values


def compare_conditions(left_run, left, right_run, right):
    a, b = conditions(left_run, left), conditions(right_run, right)
    missing = [k for k in a if a[k] is None or b[k] is None or a[k] == "" or b[k] == ""]
    changed = [k for k in a if k not in missing and a[k] != b[k]]
    return {"comparable": not missing and not changed and complete(left) and complete(right),
            "missing_conditions": missing, "changed_conditions": changed,
            "complete_task_coverage": complete(left) and complete(right)}


def describe(values):
    if len(values) < 2:
        return unavailable("fewer_than_two_observations")
    try:
        variance = statistics.variance(values)
        mean = statistics.mean(values)
        if not finite_number(variance) or not finite_number(mean):
            return unavailable("nonfinite_statistic")
        return {"status": "available", "n": len(values), "mean": mean,
                "sample_variance": variance, "sample_std": math.sqrt(variance), "ddof": 1}
    except (ValueError, OverflowError):
        return unavailable("nonfinite_statistic")


def trial_variation(point):
    if reused(point):
        return unavailable("reused_or_cached_measurement")
    n = point.get("n_trials", _at(point, "identity.trials"))
    if type(n) is not int or n < 2:
        return unavailable("fewer_than_two_recorded_trials")
    declared = _at(point, 'identity.trials')
    if declared is not None and (type(declared) is not int or declared != n):
        return unavailable('conflicting_trial_counts')
    if not complete(point):
        return unavailable("incomplete_task_coverage")
    rows = point.get("per_task_trials")
    if not isinstance(rows, dict) or set(rows) != set(point["per_task"]):
        return unavailable("missing_trial_records")
    if any(not isinstance(v, list) or len(v) != n
           or any(not finite_number(score) for score in v) for v in rows.values()):
        return unavailable("incomplete_or_nonfinite_trials")
    if any(not math.isclose(statistics.mean(v), point["per_task"][task], abs_tol=2e-6)
           for task, v in rows.items()):
        return unavailable("trial_mean_disagrees_with_task_score")
    ordered = [rows[task] for task in sorted(rows)]
    result = describe([statistics.mean(row[i] for row in ordered) for i in range(n)])
    result.update({"scope": "within_run_trial_index_means", "n_tasks": len(rows),
                   "statistic": "arithmetic_mean_of_recorded_task_rewards",
                   "per_task_sample_variances": [describe(row)["sample_variance"] for row in ordered],
                   "independence": "not_established_by_trial_indices",
                   "not_independent_optimization_runs": True})
    return result


def run_variation(states):
    """States are (run, point) pairs explicitly selected by the author."""
    if len(states) < 2:
        return unavailable("fewer_than_two_runs")
    if len({r["run_id"] for r, _ in states}) != len(states):
        return unavailable("repeated_run_id")
    first_run, first = states[0]
    if any(reused(p) for _, p in states):
        return unavailable("reused_or_cached_measurement")
    for run, point in states:
        comparison = compare_conditions(first_run, first, run, point)
        if not comparison["comparable"]:
            return {**unavailable("incompatible_or_missing_conditions"), **comparison}
        if _at(first, "identity.harness_sha") != _at(point, "identity.harness_sha"):
            return unavailable("different_harness_trees")
    result = describe([point["score"] for _, point in states])
    result.update({"scope": "between_runs_same_harness", "independence": "distinct_run_ids_only",
                   "not_optimization_method_variance": True})
    return result
