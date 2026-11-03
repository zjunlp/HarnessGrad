"""Agent rollout record shared by Agents, Benchmarks, and Algorithms."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from statistics import mean
from types import MappingProxyType
from typing import Any

from flashrsi.core.benchmark import Task


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Trajectory:
    """The result and accounting information from one Agent task run."""

    task: Task | None = None
    status: str = "finished"
    output: str = ""
    messages: tuple[Mapping[str, Any], ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    error_message: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.task is not None and not isinstance(self.task, Task):
            raise ValueError("task must be a Task or None")
        if self.status not in {"finished", "error"}:
            raise ValueError("status must be 'finished' or 'error'")
        if not isinstance(self.output, str):
            raise ValueError("output must be a string")
        if self.status == "error" and not self.error_message:
            raise ValueError("error_message is required when status is 'error'")
        if self.error_message is not None and not isinstance(self.error_message, str):
            raise ValueError("error_message must be a string or None")
        if not isinstance(self.metrics, Mapping):
            raise ValueError("metrics must be a mapping")
        for name, value in self.metrics.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise ValueError("metrics must map string names to finite numbers")
        if not isinstance(self.usage, Mapping):
            raise ValueError("usage must be a mapping")
        object.__setattr__(
            self,
            "messages",
            tuple(_immutable_mapping(message) for message in self.messages),
        )
        object.__setattr__(self, "metrics", _immutable_mapping(self.metrics))
        object.__setattr__(self, "usage", _immutable_mapping(self.usage))


class Results(tuple):
    """Graded trajectories that know their own score.

    An algorithm asks "how did this version do", not "what is the mean of the
    benchmark's primary metric over these trajectories" -- the evaluation
    carries the metric it was graded on, so reading the score needs no help
    from the algorithm.

    This is a tuple: iterating, ``len()`` and ``zip()`` work as before. A slice
    is an evaluation too, so it keeps the metric and scores itself over the
    trajectories it kept -- a method handed ``results[a:b]`` can still read
    ``.score``, which is what makes the type safe to pass around.
    """

    def __new__(cls, trajectories, *, metric: str):
        results = super().__new__(cls, trajectories)
        # Read once here rather than on each access: a caller that wants both
        # the mean and the per-task scores -- GEPA does -- would otherwise walk
        # the trajectories twice. A tuple subclass cannot declare __slots__, so
        # these are ordinary instance attributes.
        results._metric = metric
        results._per_task = MappingProxyType(
            {item.task.task_id: item.metrics[metric] for item in results}
        )
        return results

    def __getitem__(self, index):
        """Keep a slice an evaluation; a single index is still one trajectory."""
        item = super().__getitem__(index)
        if isinstance(index, slice):
            return type(self)(item, metric=self._metric)
        return item

    def __getnewargs_ex__(self):
        """Carry the metric through ``copy``, which rebuilds via ``__new__``.

        Without this the rebuild loses the keyword and raises. Deep copies of
        trajectories fail anyway, on their own immutable mapping views; nothing
        copies an evaluation, and this is here so that doing so is not a trap.
        """
        return (tuple(self),), {"metric": self._metric}

    @property
    def score(self) -> float:
        """The mean primary metric: one evaluation's fitness.

        An empty evaluation has no score rather than a zero one, which a
        caller distinguishes by checking the results themselves.
        """
        if not self:
            raise ValueError("an empty evaluation has no score")
        return mean(self._per_task.values())

    @property
    def per_task(self) -> Mapping[str, float]:
        """The same scores read one task at a time, keyed by ``task_id``.

        A comparison that the mean cannot express reads this instead: a Pareto
        frontier over individual tasks, or which tasks one edit flipped.
        """
        return self._per_task
