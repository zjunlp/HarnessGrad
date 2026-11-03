"""DeepSearchQA native eval records; experiment partitions belong to the user."""

import csv
import hashlib
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from flashrsi.benchmarks._native import NativeBenchmark
from flashrsi.benchmarks.deepsearchqa._official import MODEL, REVISION, metrics


class DeepSearchQA(NativeBenchmark):
    name = "deepsearchqa"
    description = (
        "DeepSearchQA native web research tasks and official Gemini autorating."
    )
    primary_metric = "f1_score"
    metadata = MappingProxyType(
        {
            "source": "google/deepsearchqa",
            "revision": "b2623f8653065c2672de6d941fc5434cd652376c",
            "configs": ("deepsearchqa",),
            "native_splits": ("eval",),
            "scorer_revision": REVISION,
        }
    )

    def _source_rows(self):
        data = self.config.get("data_path")
        if data is None:
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(
                repo_id=self.metadata["source"],
                repo_type="dataset",
                revision=self.metadata["revision"],
                filename="DSQA-full.csv",
            )
            # Generic CSV inference turns four literal answers into null values.
            # Read native strings so no official records are dropped or rewritten.
            with Path(path).open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    yield "eval", row
            return
        official_file = (
            isinstance(data, (str, Path)) and Path(data).name == "DSQA-full.csv"
        )
        for split, row in super()._source_rows():
            # Explicit directory/role mappings take precedence over the CSV name.
            if split == "DSQA-full" or (split == "local" and official_file):
                split = "eval"
            yield split, row

    def _iter_tasks(self):
        for split, row in self._source_rows():
            task_id = (
                "deepsearchqa-"
                + hashlib.sha256(str(row.get("problem", "")).encode()).hexdigest()
            )
            if self.config.get("_prepared_splits") is not None:
                task_id = row["_flashrsi_id"]
            if any(
                not isinstance(row.get(k), str) or not row[k].strip()
                for k in ("problem", "answer", "problem_category", "answer_type")
            ) or row["answer_type"].strip() not in ("Single Answer", "Set Answer"):
                raise ValueError(
                    f"DeepSearchQA requires native problem/answer/problem_category/answer_type: {task_id}"
                )
            # answer_type is explicitly forbidden at inference by the dataset card.
            yield self._register(row, split, task_id, row["problem"], env=self.name)

    def grade(self, trajs):
        graded = []
        for trajectory in trajs:
            self._row_for(trajectory)
            scores = dict.fromkeys(("precision", "recall", "f1_score", "accuracy"), 0.0)
            if trajectory.status == "finished":
                task = trajectory.task
                event = trajectory.messages[-1] if trajectory.messages else {}
                judge = self.config.get("judge", {})
                provider = judge.get("provider", "google")
                if (
                    event.get("type") != "DeepSearchQAVerifier"
                    or event.get("task_id") != task.task_id
                    or event.get("task_checksum") != task.metadata["task_checksum"]
                    or event.get("judge_revision") != REVISION
                    or event.get("judge_provider", "google") != provider
                    or event.get("judge_model")
                    != judge.get("llm", {}).get("model_name", MODEL)
                ):
                    raise ValueError(
                        "DeepSearchQA requires a matching final host DeepSearchQAVerifier event"
                    )
                scores = metrics(event["counts"])
            graded.append(replace(trajectory, metrics={**trajectory.metrics, **scores}))
        return tuple(graded)


__all__ = ["DeepSearchQA"]
