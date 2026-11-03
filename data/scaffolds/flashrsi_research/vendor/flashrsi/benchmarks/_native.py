"""Native records and user-selected partitions for the standalone benchmarks."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType

from flashrsi.benchmarks._data import _load_local_rows as _local_rows
from flashrsi.benchmarks._data import _paths_record, _split_tasks
from flashrsi.core import Benchmark, Task


class NativeBenchmark(Benchmark):
    def __init__(self, config):
        super().__init__()
        self.config = copy.deepcopy(dict(config))
        if self.config.get("name", self.name) != self.name:
            raise ValueError(
                f"{type(self).__name__} requires benchmark.name: {self.name}"
            )
        self._tasks_by_id = {}
        self._rows_by_id = {}

    def load(self):
        if self._splits is None:
            splits = _split_tasks(self._iter_tasks(), self.config)
            self._splits = splits
            self._tasks_by_id = {
                t.task_id: t for group in splits.values() for t in group
            }
        return self._splits

    def _source_rows(self):
        data = self.config.get("data_path")
        if data is not None:
            self.metadata = MappingProxyType(
                {
                    "source": "local",
                    "upstream": dict(self.metadata),
                    "data_path": _paths_record(data),
                }
            )
            if isinstance(data, Mapping):
                if not data or set(data) - {"train", "dev", "validation", "test"}:
                    raise ValueError(
                        "benchmark.data_path mapping must name train/dev/test splits"
                    )
                for split, paths in data.items():
                    for path in paths if isinstance(paths, list) else [paths]:
                        for _, row in _local_rows(path):
                            yield split, row
            else:
                yield from _local_rows(data)
            return

        from datasets import load_dataset

        for subset in self.metadata.get("configs", (None,)):
            options = {
                "name": subset,
                "revision": self.metadata["revision"],
                "streaming": True,
            }
            if "data_files" in self.metadata:
                options["data_files"] = self.metadata["data_files"]
            dataset = load_dataset(self.metadata["source"], **options)
            for split, rows in dataset.items():
                for row in rows:
                    yield str(split), row

    def _register(self, row, split, task_id, question, *, env=None, **metadata):
        if (
            self.config.get("_prepared_splits") is not None
            and "native_source_split" in row
        ):
            metadata["native_source_split"] = row["native_source_split"]
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"{self.name} row requires a non-empty id")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"{self.name} row requires a non-empty question")
        checksum = hashlib.sha256(
            json.dumps(
                row, sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        self._rows_by_id[task_id] = copy.deepcopy(row)
        return Task(
            task_id,
            question,
            env=env,
            metadata={"source_split": split, "task_checksum": checksum, **metadata},
        )

    def _row_for(self, trajectory):
        if self._splits is None:
            raise RuntimeError(f"{self.name} has not been loaded; call load() first")
        task = trajectory.task
        if task is None or self._tasks_by_id.get(task.task_id) != task:
            raise ValueError(f"{self.name} requires a loaded benchmark Task")
        return self._rows_by_id[task.task_id]
