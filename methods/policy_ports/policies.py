"""Search policies over platform-measured, adaptation-visible history.

Each policy is an explicit delayed-measurement port of upstream's strategy,
not a native reproduction. No function here executes or scores a task.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Member:
    round: int
    score: float
    per_task: dict[str, float]
    tree: str
    info: dict[str, Any]


def finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def best(members):
    """Strict improvement only: stable max retains the earliest equal scorer."""
    if not members:
        raise ValueError("no adaptation-visible measured members")
    return max(members, key=lambda m: m.score)


def pareto_front(members):
    front, maxima = {}, {}
    for i, member in enumerate(members):
        for task, value in member.per_task.items():
            if task not in maxima or value > maxima[task]:
                maxima[task], front[task] = value, {i}
            elif value == maxima[task]:
                front[task].add(i)
    active = set().union(*front.values()) if front else set()
    order = sorted(active, key=lambda i: (members[i].score, i))
    while True:
        for i in order:
            if i in active and all(
                (owners & active) - {i} for owners in front.values() if i in owners
            ):
                active.remove(i)
                break
        else:
            return {task: owners & active for task, owners in front.items()}


def gepa_parent(members, rng, strategy="pareto"):
    if strategy == "current_best":
        return best(members)
    if strategy != "pareto":
        raise ValueError("candidate_selection_strategy must be pareto or current_best")
    front = pareto_front(members)
    weighted = [i for owners in front.values() for i in sorted(owners)]
    return members[rng.choice(weighted)] if weighted else best(members)


def admits_gepa(child, parent, criterion="strict_improvement"):
    if set(child.per_task) != set(parent.per_task):
        return False
    if criterion == "strict_improvement":
        return child.score > parent.score
    if criterion in ("improvement_or_equal", "non_decreasing"):
        return child.score >= parent.score
    raise ValueError("unsupported GEPA acceptance_criterion")


def evoprompt_population(members, size, variant="ga", survivor="elitist"):
    if size < (4 if variant == "de" else 2):
        raise ValueError("EvoPrompt requires population_size >= 4 for DE, >= 2 for GA")
    population = []
    batches = {}
    for member in members:
        batch = member.info.get("generation")
        if batch is None:
            if not population:
                population.append(member)
            continue
        batches.setdefault(batch, []).append(member)
    for generation, children in sorted(batches.items()):
        if len(population) < size:
            population = sorted(population + children, key=lambda m: -m.score)[:size]
        elif variant == "de":
            next_population = list(population)
            for child in children:
                slot = child.info.get("target_slot")
                if (
                    isinstance(slot, int)
                    and 0 <= slot < len(population)
                    and child.score > population[slot].score
                ):
                    next_population[slot] = child
            population = next_population
        elif survivor == "standard":
            # A budget-truncated generation must not silently remove unmeasured slots.
            population = (children + population[len(children) :])[:size]
        elif survivor in ("topk", "elitist"):
            population = sorted(population + children, key=lambda m: -m.score)[:size]
        else:
            raise ValueError("survivor_selection must be standard or topk")
    return population


def ga_parents(population, rng, selection="tournament"):
    if len(population) < 2:
        return population * 2
    if selection in ("tournament", "tour"):
        return [max(rng.sample(population, 2), key=lambda m: m.score) for _ in range(2)]
    if selection == "wheel":
        weights = [m.score for m in population]
        if any(v < 0 for v in weights):
            raise ValueError("wheel selection requires nonnegative fitness")
        pool = rng.choices(
            population, weights=weights if sum(weights) > 0 else None, k=len(population)
        )
        return rng.sample(pool, 2)
    if selection == "random":
        return rng.sample(population, 2)
    raise ValueError("selection must be tournament, wheel or random")


def de_parents(population, slot, rng, donor_random=False):
    if len(population) < 4:
        raise ValueError("DE requires four measured population positions")
    others = [m for i, m in enumerate(population) if i != slot]
    first, second, third = rng.sample(others, 3)
    return [
        population[slot],
        first,
        second,
        third if donor_random else best(population),
    ]


def preserving_extension(
    child, parents, invariant, *, tolerance=0.0, solved_threshold=1.0
):
    if not parents or any(set(p.per_task) != set(child.per_task) for p in parents):
        return {"accepted": False, "reason": "unpaired task sets"}
    if any(not 0 <= v <= 1 for m in [child, *parents] for v in m.per_task.values()):
        raise ValueError("DarwinX requires task scores in [0,1]")
    delta = {
        task: value - parents[0].per_task[task]
        for task, value in child.per_task.items()
    }
    regression = sum(
        max(max(parent.per_task[t] for parent in parents) - value, 0)
        for t, value in child.per_task.items()
    )
    lost = sorted(
        task
        for task in invariant
        if task in child.per_task and child.per_task[task] < solved_threshold
    )
    gain = sum(delta.values())
    return {
        "accepted": gain > 1e-12 and regression <= tolerance + 1e-12 and not lost,
        "gain": gain,
        "regression_mass": regression,
        "lost_invariant": lost,
        "delta": delta,
        "solved": sorted(t for t, v in child.per_task.items() if v >= solved_threshold),
    }


def darwin_archive(members, *, tolerance=0.0, threshold=1.0):
    root = members[0]
    root.info["_accepted"] = True
    entries = [root]
    invariant = {t for t, score in root.per_task.items() if score >= threshold}
    root.info["_invariant"] = sorted(invariant)
    decisions = {}
    cumulative = {root.round: 0.0}
    for child in members[1:]:
        ids = child.info.get("parents", [])
        parents = [m for rid in ids for m in entries if m.round == rid]
        inherited = {t for m in parents for t in m.info.get("_invariant", [])}
        if len(parents) == 2:
            inherited.update(
                t for m in parents for t, v in m.per_task.items() if v >= threshold
            )
        verdict = preserving_extension(
            child, parents, inherited, tolerance=tolerance, solved_threshold=threshold
        )
        decisions[str(child.round)] = verdict
        child.info["_accepted"] = verdict["accepted"]
        child.info["_invariant"] = sorted(
            inherited | set(verdict.get("solved", []))
            if verdict["accepted"]
            else inherited
        )
        if parents and set(child.per_task) == set(parents[0].per_task):
            entries.append(child)
            cumulative[child.round] = cumulative.get(parents[0].round, 0) + verdict.get(
                "gain", 0
            )
        if verdict["accepted"]:
            invariant.update(verdict["solved"])
    return entries, invariant, cumulative, decisions


def darwin_parents(
    archive,
    cumulative,
    rng,
    step,
    merge_every=3,
    exploration=0.5,
    attempted=(),
    solved_threshold=1.0,
):
    if merge_every and (step + 1) % merge_every == 0:
        pairs = []
        for a, b in itertools.combinations(archive, 2):
            solved_a = {t for t, v in a.per_task.items() if v >= solved_threshold}
            solved_b = {t for t, v in b.per_task.items() if v >= solved_threshold}
            if (
                solved_a - solved_b
                and solved_b - solved_a
                and sorted([a.round, b.round]) not in attempted
            ):
                pairs.append((a, b))
        if pairs:
            pair = max(
                pairs,
                key=lambda p: (
                    len(
                        {
                            t
                            for m in p
                            for t, v in m.per_task.items()
                            if v >= solved_threshold
                        }
                    ),
                    min(m.score for m in p),
                ),
            )
            return sorted(pair, key=lambda m: -m.score)
    eligible = [m for m in archive if m.info.get("_accepted", True)]
    parent = (
        rng.choice(eligible)
        if rng.random() < exploration
        else max(eligible, key=lambda m: (cumulative[m.round], m.score))
    )
    return [parent]
