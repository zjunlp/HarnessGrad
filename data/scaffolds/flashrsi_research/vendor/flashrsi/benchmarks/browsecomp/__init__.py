"""Official BrowseComp encrypted records; experiment roles belong to the user."""

import csv
import hashlib
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from urllib.request import urlopen

from flashrsi.benchmarks._native import NativeBenchmark
from flashrsi.benchmarks.browsecomp._official import QUERY_TEMPLATE, REVISION, decrypt

DATA_URL = (
    "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"
)
DATA_SHA256 = "7b24471cd5b3eb2a46830a14802b5c029ea62f488ff75a0f88af7923d1454abf"


def _download():
    from huggingface_hub import cached_assets_path

    root = cached_assets_path(
        library_name="flashrsi-lite", namespace="browsecomp", subfolder=DATA_SHA256
    )
    path = root / "browse_comp_test_set.csv"
    if not path.exists():
        with urlopen(DATA_URL, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != DATA_SHA256:
            raise ValueError("BrowseComp official data SHA256 mismatch")
        with TemporaryDirectory(dir=root) as temporary:
            pending = Path(temporary) / path.name
            pending.write_bytes(data)
            pending.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != DATA_SHA256:
        raise ValueError("BrowseComp cached data SHA256 mismatch")
    return path


class BrowseComp(NativeBenchmark):
    name = "browsecomp"
    description = (
        "BrowseComp native web research questions and official answer judging."
    )
    primary_metric = "accuracy"
    metadata = MappingProxyType(
        {
            "source": DATA_URL,
            "data_sha256": DATA_SHA256,
            "scorer_revision": REVISION,
            "scorer_correction": "grade_sample uses regex group(1), not group(0)",
        }
    )

    def _source_rows(self):
        if self.config.get("data_path") is not None:
            yield from super()._source_rows()
            return
        with _download().open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                yield "test", row

    def _iter_tasks(self):
        for split, row in self._source_rows():
            # Synthetic IDs are stable across row ordering and user partitions.
            task_id = (
                "browsecomp-"
                + hashlib.sha256(str(row.get("problem", "")).encode()).hexdigest()
            )
            try:
                canary = row["canary"]
                if not isinstance(canary, str) or not canary.strip():
                    raise ValueError("empty canary")
                question = decrypt(row["problem"], canary)
                answer = decrypt(row["answer"], canary)
                if not question.strip() or not answer.strip():
                    raise ValueError("empty question or answer")
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(
                    f"BrowseComp requires encrypted problem/answer and canary: {task_id}"
                ) from exc
            yield self._register(
                row,
                split,
                task_id,
                QUERY_TEMPLATE.format(Question=question),
                env="browsecomp",
            )

    def grade(self, trajs):
        graded = []
        for trajectory in trajs:
            self._row_for(trajectory)
            score = 0.0
            if trajectory.status == "finished":
                task = trajectory.task
                event = trajectory.messages[-1] if trajectory.messages else {}
                if (
                    event.get("type") != "BrowseCompVerifier"
                    or event.get("task_id") != task.task_id
                    or event.get("task_checksum") != task.metadata["task_checksum"]
                    or event.get("judge_revision") != REVISION
                    or type(event.get("correct")) is not bool
                ):
                    raise ValueError(
                        "BrowseComp requires a matching final host BrowseCompVerifier event"
                    )
                score = float(event["correct"])
            graded.append(
                replace(trajectory, metrics={**trajectory.metrics, "accuracy": score})
            )
        return tuple(graded)


__all__ = ["BrowseComp"]
