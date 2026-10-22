"""Read explicit per-adapter aggregation contracts without importing task pools.

``aggregation_policies.json`` is the declaration for each adapter, including adapters
whose source must stay untouched. There is no default for an undeclared dataset.
``run_reward`` describes the platform's mean over measured tasks; ``corpus_metrics``
describes an adapter's separate corpus evaluator, when present. An absent prediction
in a corpus is not the same as an invalid/unmeasured platform task.
"""
from __future__ import annotations

import json
from pathlib import Path

POLICIES = Path(__file__).resolve().parents[1] / "aggregation_policies.json"


def aggregation_policy(dataset: str, scope: str) -> dict[str, str]:
    """Return a validated declaration; unknown adapters/scopes fail closed."""
    declarations = json.loads(POLICIES.read_text(encoding="utf-8"))
    try:
        policy = declarations[dataset][scope]
    except KeyError as exc:
        raise ValueError(f"{dataset}: missing aggregation policy for {scope}") from exc
    if (not isinstance(policy, dict)
            or set(policy) != {"aggregation", "denominator"}
            or policy["aggregation"] not in ("arithmetic_mean", "ordered_macro_mean")
            or policy["denominator"] not in ("measured_tasks", "provided_results", "gold_rows")):
        raise ValueError(f"{dataset}: invalid aggregation policy for {scope}: {policy!r}")
    return policy


def aggregation_denominator(dataset: str, scope: str, **counts: int) -> int:
    """Choose only the declared count; callers may not silently substitute another."""
    rule = aggregation_policy(dataset, scope)["denominator"]
    count = counts.get(rule)
    if type(count) is not int or count < 0:
        raise ValueError(f"{dataset}: aggregation denominator {rule} needs a nonnegative count")
    return count
