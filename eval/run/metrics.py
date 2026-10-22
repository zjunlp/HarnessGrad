"""Scoring. Framework-owned: a Trainer never sees this module (INTERFACE.md §0)."""
from __future__ import annotations

import hashlib
import math


def pilot_task_set(task_ids: list[str], *, fraction: float = 0.10,
                   cap: int = 30) -> list[str]:
    """Select a stable capped pilot before any task execution.

    The rank uses task IDs only, so selection does not depend on scores, model output,
    filesystem order, or heldout content. The cap limits expensive early validation.
    """
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    if type(cap) is not int or cap < 1:
        raise ValueError("cap must be a positive integer")
    ids = sorted(set(task_ids))
    count = min(cap, max(1, math.ceil(len(ids) * fraction))) if ids else 0
    return sorted(ids, key=lambda tid: (hashlib.sha256(tid.encode()).hexdigest(), tid))[:count]


def diagnostic_task_set(task_ids: list[str]) -> list[str]:
    """One deterministic task, selected before execution or any score lookup."""
    return pilot_task_set(task_ids, cap=1)


def mean(xs: list[float], *, dataset: str | None = None) -> float:
    """The mean over measured tasks, with the denominator read from the declaration.

    `dataset=None` is the historical behaviour and is kept for callers that have no
    dataset to name. When a dataset IS named, the denominator comes from that
    adapter's declared aggregation policy (`data/aggregation_policies.json`) rather
    than being assumed to be `len(xs)`, and an undeclared dataset fails closed: the
    point of the declaration is that a denominator is a decision someone made, not a
    default that happened to work.
    """
    if dataset is None:
        return sum(xs) / len(xs) if xs else 0.0
    from data.scaffolds.aggregation import aggregation_denominator

    denominator = aggregation_denominator(dataset, "run_reward", measured_tasks=len(xs))
    return sum(xs) / denominator if denominator else 0.0


def ci95(xs: list[float]) -> tuple[float, float]:
    """Normal-approximation 95% CI of the mean.

    Reported on every curve point because a curve without an interval invites
    the reader to interpret noise as progress -- the failure mode this whole
    framework exists to prevent.
    """
    n = len(xs)
    if n < 2:
        return (float("nan"), float("nan"))
    m = mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    half = 1.96 * math.sqrt(var / n)
    return (m - half, m + half)


def select_task_set(scores: dict[str, float], policy: str,
                    within: list[str] | None = None) -> list[str]:
    """Apply a SamplingPolicy. Called ONCE, on H0, before any modification.

    Re-selecting per round from the current scores would make the exam change
    under the student (INTERFACE.md §2.1).

    `within` restricts the policy to one side of a train/eval split. Without it a
    `below_0.5` policy would reach across the split and pull training tasks into
    the scored set -- quietly undoing the split at exactly the moment it matters.
    """
    ids = sorted(scores) if within is None else sorted(within)
    if policy == "all":
        return ids
    if policy == "pilot_10pct":
        return pilot_task_set(ids)
    if policy == "diagnostic":
        return diagnostic_task_set(ids)
    if policy.startswith("below_"):
        cut = float(policy.removeprefix("below_"))
        return [t for t in ids if scores[t] < cut]
    if policy.startswith("above_"):
        cut = float(policy.removeprefix("above_"))
        return [t for t in ids if scores[t] >= cut]
    if policy.startswith("random_"):
        import random
        n = int(policy.removeprefix("random_"))
        rng = random.Random(0)  # recorded seed; see INTERFACE.md §2.1
        return sorted(rng.sample(ids, min(n, len(ids))))
    raise ValueError(f"unknown sampling policy {policy!r}")


def split_of(task_ids: list[str], split: dict | None) -> dict:
    """Sort an evaluated task list into its two sides.

    Returns `{"eval": [...], "train": [...]}` restricted to what was actually
    evaluated. With no split declared, everything is eval: the run still
    produces a score, it just cannot support a claim about generalisation, and
    the driver says so rather than this function guessing.
    """
    ids = list(task_ids)
    if not split:
        return {"eval": ids, "train": []}
    train = set(split.get("train", []))
    return {"eval": [t for t in ids if t not in train],
            "train": [t for t in ids if t in train]}
