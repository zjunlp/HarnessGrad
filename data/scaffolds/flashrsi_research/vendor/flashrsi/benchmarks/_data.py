"""Shared dataset I/O and deterministic task splitting."""

from __future__ import annotations

import csv
import hashlib
import heapq
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from flashrsi.core import Task


def _split_tasks(
    tasks: Iterable[Task], config: Mapping[str, Any]
) -> Mapping[str, tuple[Task, ...]]:
    requested = config.get("split_counts")
    if requested is not None:
        if (
            not isinstance(requested, Mapping)
            or set(requested) != {"train", "dev", "test"}
            or any(type(n) is not int or n < 0 for n in requested.values())
            or not sum(requested.values())
        ):
            raise ValueError(
                "benchmark.split_counts needs three nonnegative integer counts"
            )
        if config.get("split", "auto") != "auto" or config.get("limit") is not None:
            raise ValueError("Use split_counts alone, or split ratios with limit")
        total = sum(requested.values())
        config = {
            **config,
            "limit": total,
            "split": {k: n / total for k, n in requested.items()},
        }
    seed = config.get("seed", 0)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("benchmark.seed must be an integer")
    limit = config.get("limit")
    if limit is not None and (
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
    ):
        raise ValueError("benchmark.limit must be a positive integer")

    def unique_tasks() -> Iterable[Task]:
        seen = set()
        for task in tasks:
            if task.task_id in seen:
                raise ValueError(
                    f"benchmark contains duplicate task id: {task.task_id}"
                )
            seen.add(task.task_id)
            yield task

    def split_key(task: Task) -> tuple[bytes, str]:
        digest = hashlib.sha256(f"{seed}:{task.task_id}".encode()).digest()
        return digest, task.task_id

    selected = unique_tasks()
    membership = config.get("_prepared_splits")
    if membership is not None:
        roles = {task_id: role for role, ids in membership.items() for task_id in ids}

        def prepared_tasks(source):
            missing = set(roles)
            for task in source:
                if task.task_id in roles:
                    missing.remove(task.task_id)
                    yield replace(
                        task,
                        metadata={
                            **task.metadata,
                            "native_source_split": task.metadata.get(
                                "native_source_split", task.metadata.get("source_split")
                            ),
                            "source_split": roles[task.task_id],
                            "prepared_task_id": config.get("_prepared_ids", {}).get(
                                task.task_id, task.task_id
                            ),
                        },
                    )
            if missing:
                raise ValueError(
                    f"Prepared native tasks are missing: {', '.join(sorted(missing)[:5])}"
                )

        selected = prepared_tasks(selected)
    if config.get("split", "auto") == "auto":
        return _auto_split(selected, seed=seed, limit=limit)

    ordered = tuple(
        sorted(selected, key=split_key)
        if limit is None
        else heapq.nsmallest(limit, selected, key=split_key)
    )
    if not ordered:
        raise ValueError("benchmark dataset contains no tasks")

    names = ("train", "dev", "test")
    counts = _split_counts(len(ordered), [config["split"][name] for name in names])
    train_end, dev_end = counts[0], counts[0] + counts[1]
    splits = MappingProxyType(
        {
            "train": ordered[:train_end],
            "dev": ordered[train_end:dev_end],
            "test": ordered[dev_end:],
        }
    )
    if requested is not None and any(len(splits[k]) != n for k, n in requested.items()):
        raise ValueError("Not enough eligible records for benchmark.split_counts")
    return splits


def _split_counts(total: int, weights: list[float], *, minimum: int = 0) -> list[int]:
    if total < minimum * len(weights):
        raise ValueError(
            "benchmark.limit must retain at least one task per nonempty source split"
        )
    exact = [total * weight / sum(weights) for weight in weights]
    counts = [math.floor(value) for value in exact]
    order = sorted(
        range(len(weights)), key=lambda index: (-(exact[index] - counts[index]), index)
    )
    for index in order[: total - sum(counts)]:
        counts[index] += 1
    for index in range(len(counts)):
        while counts[index] < minimum:
            donor = max(
                (i for i, count in enumerate(counts) if count > minimum),
                key=lambda i: (counts[i] - exact[i], -i),
            )
            counts[donor] -= 1
            counts[index] += 1
    return counts


def _auto_split(
    tasks: Iterable[Task], *, seed: int, limit: int | None
) -> Mapping[str, tuple[Task, ...]]:
    """Keep source membership; missing experiment roles remain empty.

    A collection without native roles needs an explicit user partition. It is
    never implicitly treated as training data.
    """
    names = ("train", "dev", "test")
    groups: dict[str, list[Task]] = {name: [] for name in names}
    counts = dict.fromkeys(names, 0)

    def split_key(task: Task) -> tuple[bytes, str]:
        return hashlib.sha256(f"{seed}:{task.task_id}".encode()).digest(), task.task_id

    unknown = set()
    for task in tasks:
        source = task.metadata.get("source_split", "unassigned")
        if source == "validation":
            source = "dev"
        if source not in groups:
            unknown.add(str(source))
        else:
            group = groups[source]
            group.append(task)
            counts[source] += 1
            # Retain enough candidates for any final proportional allocation,
            # without holding the full dataset's contexts for a small trial.
            if limit is not None and len(group) >= 2 * limit:
                group[:] = heapq.nsmallest(limit, group, key=split_key)
    if unknown:
        raise ValueError(
            f"No experiment role for source splits: {', '.join(sorted(unknown))}. "
            "Set benchmark.split ratios, split_counts, or an explicit data_path "
            "role mapping; split=auto only preserves native train/dev/test."
        )
    if not any(groups.values()):
        raise ValueError("benchmark dataset contains no tasks")

    for group in groups.values():
        group.sort(key=split_key)
    if limit is not None:
        present = [name for name in names if groups[name]]
        sizes = [counts[name] for name in present]
        counts.update(
            zip(
                present,
                _split_counts(min(limit, sum(sizes)), sizes, minimum=1),
                strict=True,
            )
        )
    return MappingProxyType(
        {name: tuple(groups[name][: counts[name]]) for name in names}
    )


def _load_rows(
    data_path: str | Path | None, source: Mapping[str, Any]
) -> tuple[Iterable[tuple[str, Mapping[str, Any]]], Mapping[str, Any]]:
    if data_path is not None:
        return _local_source_rows(data_path), MappingProxyType(
            {"source": "local", "data_path": _paths_record(data_path)}
        )

    from datasets import load_dataset

    options = {
        "name": source.get("config"),
        "revision": source["revision"],
        "streaming": True,
    }
    if "splits" in source:
        rows = (
            (split, row)
            for split in source["splits"]
            for row in load_dataset(source["source"], split=split, **options)
        )
    else:
        dataset = load_dataset(source["source"], **options)
        rows = (
            (str(split), row) for split, records in dataset.items() for row in records
        )
    return rows, source


def _local_source_rows(data):
    if isinstance(data, Mapping):
        if not data or set(data) - {"train", "dev", "validation", "test"}:
            raise ValueError(
                "benchmark.data_path mapping must name train/dev/test splits"
            )
        for split, paths in data.items():
            for path in paths if isinstance(paths, list) else [paths]:
                for _, row in _load_local_rows(path):
                    yield split, row
    else:
        yield from _load_local_rows(data)


def _paths_record(value):
    if isinstance(value, Mapping):
        return {k: _paths_record(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_paths_record(v) for v in value]
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("benchmark.data_path must be a path")
    return str(Path(value).expanduser().resolve())


def _load_local_rows(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("benchmark.data_path must be a path")
    # Hub filenames may point to extensionless blobs. Their exposed filename
    # determines the native format and split; resolving it would discard both.
    path = Path(value).expanduser().absolute()
    if path.is_file():
        files = [
            (
                path.stem
                if path.stem in ("train", "dev", "val", "validation", "test")
                else "local",
                path,
            )
        ]
    elif path.is_dir():
        files = []
        for file in sorted(path.rglob("*")):
            if file.name in (
                "manifest.json",
                "dataset_info.json",
                "dataset_infos.json",
            ):
                continue
            if file.is_file() and file.suffix.lower() in (
                ".json",
                ".jsonl",
                ".parquet",
                ".csv",
            ):
                first = file.relative_to(path).parts[0]
                split = (
                    first
                    if first in ("train", "dev", "val", "validation", "test")
                    else file.stem
                )
                files.append((split, file))
    else:
        raise ValueError(f"benchmark.data_path does not exist: {path}")
    if not files:
        raise ValueError(f"No native dataset files in {path}")
    for split, file in files:
        if split == "val":
            split = "validation"
        if file.name == "browse_comp_test_set.csv" and split not in (
            "train",
            "dev",
            "validation",
            "test",
        ):
            split = "test"
        if split not in (
            "train",
            "dev",
            "validation",
            "test",
        ) and file.suffix.lower() in (".json", ".jsonl"):
            match = re.fullmatch(
                r"(?:musique_ans_v1\.0_|hotpot_)(train|dev|test)(?:_(?:distractor|fullwiki))?(?:_v\d+(?:\.\d+)?)?",
                file.stem,
            )
            if match:
                split = match[1]
        if file.suffix.lower() == ".parquet" and split not in (
            "train",
            "dev",
            "validation",
            "test",
        ):
            # Hub snapshots put shards below subject/data directories, not splits.
            match = re.fullmatch(r"(train|dev|validation|test)-\d+-of-\d+", file.stem)
            if match:
                split = match[1]
        if file.suffix.lower() == ".csv":
            with file.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    yield split, row
        elif file.suffix.lower() == ".json":
            value = json.loads(file.read_text(encoding="utf-8"))
            for row in value if isinstance(value, list) else [value]:
                if not isinstance(row, Mapping):
                    raise ValueError(f"Native dataset records must be objects: {file}")
                yield split, row
        else:
            for row in _read_records(file):
                yield split, row


def _read_records(path: Path) -> Iterable[Mapping[str, Any]]:
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq

        for batch in pq.ParquetFile(path).iter_batches():
            yield from batch.to_pylist()
        return
    try:
        if path.suffix.lower() == ".jsonl":
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        row = json.loads(line)
                        if not isinstance(row, Mapping):
                            raise ValueError(f"dataset rows must be objects: {path}")
                        yield row
        else:
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, list) or not all(
                isinstance(row, Mapping) for row in values
            ):
                raise ValueError(f"dataset must be a list of objects: {path}")
            yield from values
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read dataset {path}: {error}") from error
