"""GEPA's population record and its Pareto frontier over per-task scores.

The population is the algorithm's own state — the framework hands out one
evaluation at a time — so the record and the frontier live next to the loop
that uses them.

Upstream: https://github.com/gepa-ai/gepa
Commit: 0632cdb5dcc052e690eab439e1b4a7e3e9cfe407
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from flashrsi.core import Snapshot


@dataclass(frozen=True, slots=True)
class Member:
    """One candidate in the population, with the scores that admitted it.

    ``per_task`` is what makes GEPA GEPA: a candidate joins the frontier by
    winning a *single* validation task, so the aggregate alone cannot express
    membership.
    """

    index: int
    snapshot: Snapshot
    text: str
    per_task: Mapping[str, float]
    score: float
    parents: tuple[int, ...] = field(default=())


def frontier(population: Sequence[Member]) -> dict[str, set[int]]:
    """Map each validation task to the members that score best on it."""
    front: dict[str, set[int]] = {}
    best: dict[str, float] = {}
    for index, member in enumerate(population):
        for task_id, value in member.per_task.items():
            if task_id not in best or value > best[task_id]:
                best[task_id] = value
                front[task_id] = {index}
            elif value == best[task_id]:
                front[task_id].add(index)
    return front


def prune(
    front: Mapping[str, set[int]], population: Sequence[Member]
) -> dict[str, set[int]]:
    """Drop dominated members that no task uniquely depends on.

    Without this, a member that ties on every task it appears in inflates the
    sampling weight of a region of the frontier it does not actually own.
    """
    active = set().union(*front.values()) if front else set()
    weakest_first = sorted(active, key=lambda index: (population[index].score, index))
    while True:
        for index in weakest_first:
            if index not in active:
                continue
            covering = [members for members in front.values() if index in members]
            if all((members & active) - {index} for members in covering):
                active.remove(index)
                break
        else:
            return {task: members & active for task, members in front.items()}


def best_index(population: Sequence[Member]) -> int:
    """The aggregate best; ties keep the earlier member."""
    return max(range(len(population)), key=lambda index: population[index].score)
