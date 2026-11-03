"""Shared scaffolding for question-answering benchmarks.

QA benchmarks map their own source rows to Tasks, apply the same configured
splits, and select their own scorer. This base
owns the common half so each benchmark file is just its own data contract.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from flashrsi.benchmarks._data import _load_rows, _split_tasks
from flashrsi.benchmarks.metrics import qa_metrics
from flashrsi.core.benchmark import Benchmark, Task
from flashrsi.core.trajectory import Trajectory


class QABenchmark(Benchmark):
    """A benchmark whose Tasks are graded by comparing text to references.

    Subclasses set the usual class attributes and implement ``_task_from_row``,
    which must put every acceptable answer in ``metadata["answers"]``.
    """

    primary_metric = "exact_match"
    _default_scoring = "official"
    _scoring_modes = ("official", "legacy")
    _modes = ("context",)

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__()
        self.config = config["benchmark"] if "benchmark" in config else config
        if self.config.get("name", self.name) != self.name:
            raise ValueError(
                f"{type(self).__name__} requires benchmark.name: {self.name}"
            )
        self._tasks_by_id: dict[str, Task] = {}
        self.mode = self.config.get("mode", "context")
        if self.mode not in self._modes:
            raise ValueError(f"benchmark.mode must be one of {self._modes}")
        self.scoring = self.config.get("scoring", self._default_scoring)
        modes = (
            ("text_em_f1",)
            if self.config.get("_prepared_mrqa")
            else self._scoring_modes
        )
        if self.scoring not in modes:
            raise ValueError(f"benchmark.scoring must be one of {modes}")

    def load(self) -> Mapping[str, tuple[Task, ...]]:
        """Load the pinned dataset (or local data), then apply configured splits."""
        if self._splits is None:
            self._splits = _split_tasks(self._iter_tasks(), self.config)
            self._tasks_by_id = {
                task.task_id: task for split in self._splits.values() for task in split
            }
            metadata = {**self.metadata, "mode": self.mode, "scoring": self.scoring}
            metadata.pop("scorer_revision", None)
            if self.scoring == "official" and getattr(self, "_scorer_revision", None):
                metadata["scorer_revision"] = self._scorer_revision
            self.metadata = MappingProxyType(metadata)
        return self._splits

    def grade(self, trajs: tuple[Trajectory, ...]) -> tuple[Trajectory, ...]:
        """Apply the selected protocol; failed runs score zero."""
        if self._splits is None:
            raise RuntimeError(f"{self.name} has not been loaded; call load() first")
        graded = []
        for trajectory in trajs:
            if trajectory.task is None:
                raise ValueError(
                    f"{type(self).__name__} grading requires a Task; use Agent.rollout"
                )
            try:
                task = self._tasks_by_id[trajectory.task.task_id]
            except KeyError as error:
                raise ValueError(
                    f"trajectory has unknown {type(self).__name__} task: "
                    f"{trajectory.task.task_id}"
                ) from error
            if self.config.get("_prepared_mrqa"):
                scores = (
                    qa_metrics(trajectory.output, task.metadata["answers"])
                    if trajectory.status == "finished"
                    else {"exact_match": 0.0, "f1": 0.0}
                )
            else:
                scores = (
                    self._score(trajectory.output, task)
                    if trajectory.status == "finished"
                    else self._zero_scores()
                )
            graded.append(replace(trajectory, metrics={**trajectory.metrics, **scores}))
        return tuple(graded)

    def _score(self, prediction, task):
        return qa_metrics(prediction, task.metadata["answers"])

    def _zero_scores(self):
        return {"exact_match": 0.0, "f1": 0.0}

    def _prepare_task(self, task, row):
        return task

    def _iter_tasks(self) -> Iterable[Task]:
        for source, row in self._load_source_rows():
            if not isinstance(row, Mapping):
                raise ValueError(f"{type(self).__name__} rows must be mappings")
            if self.config.get("_prepared_mrqa"):
                # MRQA preserves public context and answer aliases, not native
                # NQ span annotations or TriviaQA rc.wikipedia evidence fields.
                from flashrsi.benchmarks.searchqa import SearchQA

                task = SearchQA._task_from_row(row, source)
            else:
                task = self._prepare_task(self._task_from_row(row, source), row)
            if (
                self.config.get("_prepared_splits") is not None
                and "native_source_split" in row
            ):
                task = replace(
                    task,
                    metadata={
                        **task.metadata,
                        "native_source_split": row["native_source_split"],
                    },
                )
            yield task

    def _load_source_rows(self) -> Iterable[tuple[str, Mapping[str, Any]]]:
        rows, self.metadata = _load_rows(
            self.config.get("data_path"), self._source_metadata()
        )
        return rows

    def _source_metadata(self):
        source = self.config.get("dataset", type(self).metadata)
        if (
            not isinstance(source, Mapping)
            or not source.get("source")
            or not source.get("revision")
        ):
            raise ValueError("benchmark.dataset needs source and revision")
        return MappingProxyType(copy.deepcopy(dict(source)))

    def _load_evolution(self, external, source_split):
        """Replace train from an independent native pool, retaining scorer ownership.

        Both loaders supply a question_checksum from the raw question rather than
        its context or rendered prompt. Subclasses retain their own evidence maps.
        """
        ids, questions = set(), set()

        def downstream():
            for task in self._iter_tasks():
                ids.add(task.task_id)
                questions.add(task.metadata["question_checksum"])
                yield task

        splits = _split_tasks(downstream(), self.config)
        digest = hashlib.sha256()
        count = 0

        def evolution_pool():
            nonlocal count
            # Check the complete sources before limiting a tiny run; different
            # IDs or paragraphs must not hide the same held-out question.
            for task in external._iter_tasks():
                if (
                    task.task_id in ids
                    or task.metadata["question_checksum"] in questions
                ):
                    raise ValueError(
                        f"{type(self).__name__} evolution pool overlaps downstream tasks"
                    )
                digest.update(
                    json.dumps(
                        (task.task_id, task.question, task.ground_truth),
                        ensure_ascii=False,
                    ).encode()
                    + b"\n"
                )
                count += 1
                yield task

        evolution = _split_tasks(
            evolution_pool(),
            {
                "split": {"train": 1, "dev": 0, "test": 0},
                "limit": self.config.get("evolution_limit"),
                "seed": self.config.get("seed", 0),
            },
        )["train"]
        metadata = {**self.metadata, "mode": self.mode, "scoring": self.scoring}
        metadata.pop("scorer_revision", None)
        if self.scoring == "official" and getattr(self, "_scorer_revision", None):
            metadata["scorer_revision"] = self._scorer_revision
        self.metadata = MappingProxyType(
            {
                **metadata,
                "evolution_source": {
                    **external.metadata,
                    "native_split": source_split,
                    "experiment_role": "external_evolution",
                    "pool_size": count,
                    "pool_checksum": digest.hexdigest(),
                },
            }
        )
        self._splits = MappingProxyType({**splits, "train": evolution})
        self._tasks_by_id = {
            task.task_id: task for group in self._splits.values() for task in group
        }
        return evolution

    @staticmethod
    def _task_from_row(row: Mapping[str, Any], source_split: str) -> Task:
        """Build one Task, recording its references in metadata["answers"]."""
        raise NotImplementedError
