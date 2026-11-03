"""Benchmark data model and extension interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flashrsi.core.trajectory import Trajectory


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Task:
    """One benchmark task passed to an Agent."""

    task_id: str
    question: str
    ground_truth: str | None = None
    env: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("task_id must be a non-empty string")
        if not isinstance(self.question, str) or not self.question:
            raise ValueError("question must be a non-empty string")
        if self.ground_truth is not None and not isinstance(self.ground_truth, str):
            raise ValueError("ground_truth must be a string or None")
        if self.env is not None and not isinstance(self.env, str):
            raise ValueError("env must be a string or None")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))


class Benchmark(ABC):
    """Dataset and grading boundary used by Algorithms."""

    name: str
    description: str
    metadata: Mapping[str, Any]
    primary_metric: str

    def __init__(self) -> None:
        self._splits: Mapping[str, tuple[Task, ...]] | None = None

    @abstractmethod
    def load(self) -> Mapping[str, tuple[Task, ...]]:
        """Load and return the train, dev, and test task splits."""

    @abstractmethod
    def grade(self, trajs: tuple[Trajectory, ...]) -> tuple[Trajectory, ...]:
        """Return trajectories with benchmark metrics attached."""

    @property
    def train(self) -> tuple[Task, ...]:
        return self._split("train")

    @property
    def dev(self) -> tuple[Task, ...]:
        return self._split("dev")

    @property
    def test(self) -> tuple[Task, ...]:
        return self._split("test")

    def _split(self, name: str) -> tuple[Task, ...]:
        if self._splits is None:
            raise RuntimeError(f"{self.name} has not been loaded; call load() first")
        return self._splits[name]
