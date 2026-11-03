"""SWE-bench Verified task descriptions and host-verifier result grading."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from flashrsi.benchmarks._data import _load_local_rows, _load_rows, _split_tasks
from flashrsi.core.benchmark import Benchmark, Task
from flashrsi.core.trajectory import Trajectory


class SWEBench(Benchmark):
    """Describe Verified repairs without exposing evaluation labels.

    Verified publishes only test; users must choose explicit experiment partitions.
    Execution supplies the official host verdict consumed by grade().
    """

    name = "swe_bench"
    description = "SWE-bench Verified repository repairs graded by resolved status."
    primary_metric = "resolved"
    metadata = MappingProxyType(
        {
            "source": "SWE-bench/SWE-bench_Verified",
            "revision": "78f471bf655a3137b2e8a75af1501690ec009ec3",
            "source_file": "data/test-00000-of-00001.parquet",
            "upstream_split": "test",
            "split_policy": "user-configured",
        }
    )

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__()
        self.config = config["benchmark"] if "benchmark" in config else config
        if self.config.get("name", self.name) != self.name:
            raise ValueError("SWEBench requires benchmark.name: swe_bench")
        self._tasks_by_id: dict[str, Task] = {}
        self._rows_by_id = {}

    def load(self) -> Mapping[str, tuple[Task, ...]]:
        if self._splits is None:
            splits = _split_tasks(self._iter_tasks(), self.config)
            evolution_path = self.config.get("evolution_data_path")
            if evolution_path is not None:
                # Keep the same native execution and official grading contract;
                # a Harbor task directory cannot be graded as a SWE repair.
                external = SWEBench(
                    {
                        "data_path": evolution_path,
                        "split": {"train": 1, "dev": 0, "test": 0},
                        "seed": self.config.get("seed", 0),
                        "limit": self.config.get("evolution_limit"),
                    }
                )
                # _split_tasks consumes the whole source even for tiny runs.
                # Compare against every downstream row, not just selected tasks.
                ids = set(self._rows_by_id)
                questions = {
                    " ".join(row["problem_statement"].split()).casefold()
                    for row in self._rows_by_id.values()
                }
                checksums = {_checksum(row) for row in self._rows_by_id.values()}
                images = {row["image"] for row in self._rows_by_id.values()}
                digest = hashlib.sha256()
                count = 0

                def evolution_pool():
                    nonlocal count
                    for task in external._iter_tasks():
                        row = external._rows_by_id[task.task_id]
                        checksum = _checksum(row)
                        if (
                            task.task_id in ids
                            or " ".join(row["problem_statement"].split()).casefold()
                            in questions
                            or checksum in checksums
                            or task.env in images
                        ):
                            raise ValueError(
                                "external evolution tasks overlap downstream benchmark tasks"
                            )
                        digest.update(checksum.encode() + b"\n")
                        count += 1
                        yield task

                evolution = _split_tasks(evolution_pool(), external.config)["train"]
                splits = MappingProxyType({**splits, "train": evolution})
                self._rows_by_id.update(
                    {
                        task.task_id: external._rows_by_id[task.task_id]
                        for task in evolution
                    }
                )
                self.metadata = MappingProxyType(
                    {
                        **self.metadata,
                        "evolution_source": {
                            **external.metadata,
                            "experiment_role": "external_evolution",
                            "pool_size": count,
                            "pool_checksum": digest.hexdigest(),
                        },
                    }
                )
            self._splits = splits
            self._tasks_by_id = {
                task.task_id: task for tasks in self._splits.values() for task in tasks
            }
        return self._splits

    def grade(self, trajs: tuple[Trajectory, ...]) -> tuple[Trajectory, ...]:
        """Read a bound host verdict; an Agent's output or metrics cannot grade it."""
        if self._splits is None:
            raise RuntimeError("swe_bench has not been loaded; call load() first")
        graded = []
        for trajectory in trajs:
            task = trajectory.task
            if task is None or self._tasks_by_id.get(task.task_id) != task:
                raise ValueError("SWEBench grading requires a loaded benchmark Task")
            resolved = 0.0
            if trajectory.status == "finished":
                event = trajectory.messages[-1] if trajectory.messages else {}
                if (
                    event.get("type") != "SWEBenchVerifier"
                    or event.get("task_id") != task.task_id
                    or event.get("task_checksum") != task.metadata["task_checksum"]
                ):
                    raise ValueError(
                        f"SWEBench requires a final SWEBenchVerifier event for task {task.task_id}"
                    )
                value = event.get("resolved")
                if type(value) is not bool:
                    raise ValueError("SWEBench verifier resolved must be a boolean")
                resolved = float(value)
            graded.append(
                replace(
                    trajectory, metrics={**trajectory.metrics, "resolved": resolved}
                )
            )
        return tuple(graded)

    def _iter_tasks(self) -> Iterable[Task]:
        if self.config.get("data_path") is not None:
            rows, self.metadata = _load_rows(
                self.config["data_path"], type(self).metadata
            )
        else:
            from huggingface_hub import hf_hub_download

            path = Path(
                hf_hub_download(
                    repo_id=self.metadata["source"],
                    filename=self.metadata["source_file"],
                    repo_type="dataset",
                    revision=self.metadata["revision"],
                )
            )
            # Verified's upstream test is the whole collection, not a held-out
            # split of this evolution experiment. Preserve local split names.
            rows = (("test", row) for _, row in _load_local_rows(path))
        for source, row in rows:
            task = _task_from_row(row, source)
            self._rows_by_id[task.task_id] = copy.deepcopy(row)
            yield task


def _task_from_row(row: Mapping[str, Any], source_split: str) -> Task:
    if not isinstance(row, Mapping):
        raise ValueError("SWEBench rows must be mappings")
    for field in (
        "instance_id",
        "repo",
        "base_commit",
        "problem_statement",
        "image",
        "version",
        "environment_setup_commit",
        "patch",
        "test_patch",
        "eval_script",
        "log_parser",
    ):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f"SWEBench row requires non-empty {field}")
    for field in ("base_commit", "environment_setup_commit"):
        if re.fullmatch(r"[0-9a-f]{40}", row[field]) is None:
            raise ValueError(f"SWEBench row has invalid {field}")
    for field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        tests = row.get(field)
        if not isinstance(tests, list) or any(
            not isinstance(test, str) or not test.strip() for test in tests
        ):
            raise ValueError(f"SWEBench row requires a list of test names in {field}")
    if not row["FAIL_TO_PASS"]:
        raise ValueError("SWEBench row requires non-empty FAIL_TO_PASS")
    if row.get("eval_type") not in ("pass_and_fail", "fail_only"):
        raise ValueError("SWEBench row requires a supported eval_type")
    checksum = _checksum(row)
    return Task(
        task_id=row["instance_id"],
        question=(
            "Resolve the following issue in the supplied repository.\n\n"
            f"Repository: {row['repo']}\nBase commit: {row['base_commit']}\n\n"
            f"Issue:\n{row['problem_statement']}"
        ),
        env=row["image"],
        metadata={
            "source_split": source_split,
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "version": row["version"],
            "environment_setup_commit": row["environment_setup_commit"],
            "task_checksum": checksum,
        },
    )


def _checksum(row):
    return hashlib.sha256(
        json.dumps(
            row,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


__all__ = ["SWEBench"]
