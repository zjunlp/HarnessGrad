"""Official WideSearch tasks, private gold CSVs and user-selected partitions."""

import hashlib
import importlib.util
import io
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from flashrsi.benchmarks._data import _load_local_rows, _paths_record
from flashrsi.benchmarks._native import NativeBenchmark

REVISION = "9825ba7b140b71d81b364793f86dabe4cfed6749"
DATA_REVISION = "6531a7e5b497d44c8912407e0cb3dc95bd98cc09"
METRICS = (
    "score",
    "precision_by_row",
    "recall_by_row",
    "f1_by_row",
    "precision_by_item",
    "recall_by_item",
    "f1_by_item",
)


def _scores(value):
    # The native key-mapping merge can retain multiple aliases of one gold row.
    # Preserve its resulting P/R/F1 values rather than clipping official output.
    if (
        not isinstance(value, Mapping)
        or set(value) != set(METRICS)
        or any(
            type(n) not in (int, float) or not math.isfinite(n) or n < 0
            for n in value.values()
        )
    ):
        raise ValueError("Invalid WideSearch official metrics")
    if value["score"] not in (0, 1):
        raise ValueError("Invalid WideSearch success score")
    return dict(value)


class WideSearch(NativeBenchmark):
    name = "widesearch"
    description = "WideSearch native broad web research and official table evaluation."
    primary_metric = "score"
    metadata = MappingProxyType(
        {
            "source": "ByteDance-Seed/WideSearch",
            "revision": DATA_REVISION,
            "native_splits": ("full",),
            "scorer_revision": REVISION,
        }
    )

    def __init__(self, config):
        super().__init__(config)
        self._queries_by_id = {}

    def _check(self):
        if any(
            importlib.util.find_spec(name) is None
            for name in ("pandas", "numpy", "dateparser", "loguru", "openai")
        ):
            raise ValueError(
                "Install flashrsi-lite[all] for WideSearch loading and evaluation"
            )

    def _records(self):
        data = self.config.get("data_path")
        custom_gold = self.config.get("gold_path")
        if custom_gold is not None:
            custom_gold = _paths_record(custom_gold)
        if data is None:
            from huggingface_hub import snapshot_download

            data = snapshot_download(
                repo_id=self.metadata["source"],
                repo_type="dataset",
                revision=DATA_REVISION,
                allow_patterns=["widesearch.jsonl", "widesearch_gold/*.csv"],
                # HF's initial symlink capability probe races between download
                # threads on Windows without symlink privileges (WinError 1314).
                max_workers=1 if os.name == "nt" else 8,
            )
            if custom_gold is not None:
                self.metadata = MappingProxyType(
                    {**self.metadata, "gold_source": "local", "gold_path": custom_gold}
                )
        else:
            self.metadata = MappingProxyType(
                {
                    "source": "local",
                    "upstream": dict(self.metadata),
                    "data_path": _paths_record(data),
                    "gold_path": _paths_record(self.config["gold_path"])
                    if self.config.get("gold_path") is not None
                    else None,
                }
            )
        roles = data if isinstance(data, Mapping) else {None: data}
        if isinstance(data, Mapping) and (
            not roles or set(roles) - {"train", "dev", "validation", "test"}
        ):
            raise ValueError(
                "WideSearch data_path mapping must name train/dev/validation/test"
            )
        for role, paths in roles.items():
            for path in paths if isinstance(paths, list) else [paths]:
                root = Path(path).expanduser().resolve()
                if not root.exists():
                    raise ValueError("WideSearch data_path does not exist")
                gold_root = (
                    Path(self.config["gold_path"]).expanduser().resolve()
                    if self.config.get("gold_path") is not None
                    else (root if root.is_dir() else root.parent) / "widesearch_gold"
                )
                files = (
                    [root]
                    if root.is_file()
                    else sorted(
                        f
                        for f in root.rglob("*")
                        if f.suffix.lower() in (".json", ".jsonl", ".parquet")
                        and "widesearch_gold" not in f.relative_to(root).parts
                        and f.name
                        not in (
                            "manifest.json",
                            "dataset_info.json",
                            "dataset_infos.json",
                        )
                    )
                )
                if not files:
                    raise ValueError(
                        "WideSearch data_path contains no native task files"
                    )
                for file in files:
                    first = file.relative_to(root).parts[0] if root.is_dir() else None
                    for split, row in _load_local_rows(file):
                        if first in ("train", "dev", "val", "validation", "test"):
                            split = "validation" if first == "val" else first
                        elif split == "local" and file.name == "widesearch.jsonl":
                            split = "full"
                        yield role or split, row, gold_root

    def _iter_tasks(self):
        self._check()
        import pandas as pd

        from ._vendor.data_loader import WideSearchQuery
        from ._vendor.metric_utils import (
            metric_function_registry,
            preprocess_function_registry,
        )
        from ._vendor.utils import norm_column

        for split, row, gold_root in self._records():
            task_id = row.get("instance_id")
            if not isinstance(task_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]+", task_id
            ):
                raise ValueError(
                    "WideSearch requires a native instance_id without path components"
                )
            try:
                evaluation = row["evaluation"]
                if isinstance(evaluation, str):
                    evaluation = json.loads(evaluation)
                required, keys = evaluation["required"], evaluation["unique_columns"]
                pipeline = evaluation["eval_pipeline"]
                if (
                    not isinstance(required, list)
                    or not required
                    or any(not isinstance(k, str) or not k for k in required)
                    or not isinstance(keys, list)
                    or not keys
                    or not set(keys) <= set(required)
                    or not isinstance(pipeline, dict)
                    or not set(pipeline) <= set(required)
                    or any(k not in pipeline for k in set(required) - set(keys))
                ):
                    raise ValueError("Invalid evaluation schema")
                for item in pipeline.values():
                    if not isinstance(item, dict):
                        raise ValueError("Invalid column evaluation schema")
                    if set(item.get("preprocess", [])) - set(
                        preprocess_function_registry
                    ) or set(item.get("metric", [])) - set(metric_function_registry):
                        raise ValueError("Unknown official evaluator function")
                raw = (gold_root / (task_id + ".csv")).read_bytes()
                answer = pd.read_csv(io.BytesIO(raw))
                answer.columns = [norm_column(col.strip()) for col in answer.columns]
                answer = answer[required]
                if answer.empty:
                    raise ValueError("Empty reference table")
                language = row["language"]
                if language not in ("en", "zh"):
                    raise ValueError("Invalid native language")
            except (KeyError, TypeError, ValueError, OSError) as exc:
                raise ValueError(
                    f"WideSearch invalid native evaluation or gold CSV: task_id={task_id}"
                ) from exc
            self._queries_by_id[task_id] = WideSearchQuery(
                task_id, row.get("query"), evaluation, answer, language
            )
            yield self._register(
                {**row, "_gold_sha256": hashlib.sha256(raw).hexdigest()},
                split,
                task_id,
                row.get("query"),
                env=self.name,
                language=language,
            )

    def grade(self, trajs):
        graded = []
        for trajectory in trajs:
            self._row_for(trajectory)
            scores = dict.fromkeys(METRICS, 0.0)
            if trajectory.status == "finished":
                task = trajectory.task
                event = trajectory.messages[-1] if trajectory.messages else {}
                if (
                    event.get("type") != "WideSearchVerifier"
                    or event.get("task_id") != task.task_id
                    or event.get("task_checksum") != task.metadata["task_checksum"]
                    or event.get("judge_revision") != REVISION
                ):
                    raise ValueError(
                        "WideSearch requires a matching final host WideSearchVerifier event"
                    )
                scores = _scores(event.get("scores"))
            graded.append(replace(trajectory, metrics={**trajectory.metrics, **scores}))
        return tuple(graded)


__all__ = ["WideSearch"]
