"""Native LiveMathBench questions with official judge and G-Pass@k metrics."""

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from flashrsi.benchmarks._data import _load_local_rows, _paths_record
from flashrsi.benchmarks._native import NativeBenchmark
from flashrsi.benchmarks.livemathbench._metrics import metrics
from flashrsi.benchmarks.livemathbench._prompts import PROMPT_CN, PROMPT_EN

REVISION = "efb91b2809e91997c5d043d468e4aa986fbb341f"
_SUBSETS = {
    "202412": ("CNMO", "CCEE", "AMC", "WLPMC", "hard"),
    "202505": ("all", "hard"),
}


class LiveMathBench(NativeBenchmark):
    name = "livemathbench"
    description = "LiveMathBench native mathematics and repeated-sampling stability."
    metadata = MappingProxyType(
        {
            "source": "opencompass/LiveMathBench",
            "revision": "ea05b95ab431f3ef3900ce86f864451cf5bcd699",
            "judge_revision": REVISION,
            "gpass_revision": "20a02a1479c2db19d9b8a8676c809c4696f9e889",
        }
    )

    def __init__(self, config):
        super().__init__(config)
        self._version = str(self.config.get("version", "202412"))
        if self._version not in ("202412", "202505"):
            raise ValueError("LiveMathBench version must be 202412 or 202505")
        self._subsets = self.config.get(
            "dataset_splits",
            ["CNMO", "CCEE", "AMC", "WLPMC"] if self._version == "202412" else ["all"],
        )
        self._languages = self.config.get(
            "dataset_languages", ["cn", "en"] if self._version == "202412" else ["en"]
        )
        allowed = _SUBSETS[self._version]
        for name, values, choices in (
            ("dataset_splits", self._subsets, allowed),
            (
                "dataset_languages",
                self._languages,
                ("cn", "en") if self._version == "202412" else ("en",),
            ),
        ):
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(v, str) or v not in choices for v in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError(
                    f"LiveMathBench {name} must be a nonempty unique list in {choices}"
                )
        self._n = self.config.get("n", 48)
        k = self.config.get("k", 16)
        self._ks = [k] if type(k) is int else k
        if type(self._n) is not int or self._n < 1:
            raise ValueError("LiveMathBench n must be a positive integer")
        if (
            not isinstance(self._ks, list)
            or not self._ks
            or any(type(v) is not int or not 1 <= v <= self._n for v in self._ks)
            or len(set(self._ks)) != len(self._ks)
        ):
            raise ValueError(
                "LiveMathBench k must contain unique positive integers <= n"
            )
        self.primary_metric = self.config.get(
            "primary_metric", f"mG-Pass@{max(self._ks)}"
        )
        if not isinstance(
            self.primary_metric, str
        ) or self.primary_metric not in metrics([0] * self._n, self._ks):
            raise ValueError(
                "LiveMathBench primary_metric must name a configured metric"
            )
        self.metadata = MappingProxyType(
            {
                **self.metadata,
                "version": self._version,
                "dataset_splits": self._subsets,
                "dataset_languages": self._languages,
                "n": self._n,
                "k": self._ks,
                "primary_metric": self.primary_metric,
                "metric_scale": "0..1",
            }
        )

    def _iter_records(self):
        data = self.config.get("data_path")
        if self.config.get("_prepared_source"):
            for role, path in data.items():
                for _, row in _load_local_rows(Path(path)):
                    yield (
                        role,
                        row.get("native_subset", "all"),
                        row["native_language"],
                        row,
                    )
            return
        if data is None:
            from datasets import load_dataset
            from datasets.exceptions import DatasetNotFoundError
            from huggingface_hub.errors import GatedRepoError

            for subset in self._subsets:
                for language in self._languages:
                    try:
                        dataset = load_dataset(
                            self.metadata["source"],
                            name=f"v{self._version}_{subset}_{language}",
                            revision=self.metadata["revision"],
                            streaming=True,
                        )
                        for split, rows in dataset.items():
                            for row in rows:
                                yield str(split), subset, language, row
                    except (GatedRepoError, DatasetNotFoundError):
                        raise ValueError(
                            "LiveMathBench requires approved Hugging Face access to opencompass/LiveMathBench; authenticate locally or set benchmark.data_path to native files"
                        ) from None
            return
        self.metadata = MappingProxyType(
            {
                "source": "local",
                "upstream": dict(self.metadata),
                "data_path": _paths_record(data),
            }
        )
        paths = data if isinstance(data, Mapping) else {None: data}
        if not paths or any(
            role not in (None, "train", "dev", "validation", "test") for role in paths
        ):
            raise ValueError(
                "benchmark.data_path mapping must name train/dev/test splits"
            )
        for role, values in paths.items():
            for value in values if isinstance(values, list) else [values]:
                path = Path(value).expanduser().resolve()
                files = (
                    [path]
                    if path.is_file()
                    else sorted(
                        p
                        for p in path.rglob("*")
                        if p.suffix.lower() in (".json", ".jsonl", ".parquet")
                        and p.name
                        not in (
                            "dataset_info.json",
                            "dataset_infos.json",
                            "manifest.json",
                        )
                    )
                )
                if not files:
                    raise ValueError(f"No LiveMathBench native files in {path}")
                for file in files:
                    if any(
                        v in file.parts
                        for v in ("202412", "202505")
                        if v != self._version
                    ):
                        continue
                    pair = file.stem.rsplit("_", 1)
                    # Language suffixes alone also occur in user partition files.
                    native = (
                        len(pair) == 2
                        and any(pair[0] in subsets for subsets in _SUBSETS.values())
                        and pair[-1] in ("cn", "en")
                    )
                    if native:
                        subset, language = pair
                        # The publisher's v202505_hard_en config points to the
                        # same all_en.jsonl file. Preserve that alias as published.
                        if (
                            self._version == "202505"
                            and subset == "all"
                            and self._subsets == ["hard"]
                        ):
                            subset = "hard"
                        if (
                            subset not in self._subsets
                            or language not in self._languages
                        ):
                            continue
                    else:
                        if len(self._subsets) != 1 or len(self._languages) != 1:
                            raise ValueError(
                                "LiveMathBench custom filenames require one dataset_splits and one dataset_languages entry; original SUBSET_LANG.jsonl filenames need no rewriting"
                            )
                        subset, language = self._subsets[0], self._languages[0]
                    for source, row in _load_local_rows(file):
                        inferred = (
                            next(
                                (
                                    p
                                    for p in reversed(file.relative_to(path).parts[:-1])
                                    if p
                                    in ("train", "dev", "val", "validation", "test")
                                ),
                                None,
                            )
                            if path.is_dir()
                            else None
                        )
                        source = role or inferred or ("test" if native else source)
                        yield (
                            "validation" if source == "val" else source,
                            subset,
                            language,
                            row,
                        )

    def _iter_tasks(self):
        for split, subset, language, row in self._iter_records():
            question, answer = row.get("question"), row.get("answer")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("LiveMathBench requires native question text")
            task_id = (
                f"v{self._version}_{subset}_{language}:"
                + hashlib.sha256(question.encode()).hexdigest()
            )
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError(
                    f"LiveMathBench task_id={task_id}: missing native answer"
                )
            if (
                not isinstance(row.get("question_type"), str)
                or not row["question_type"].strip()
            ):
                raise ValueError(
                    f"LiveMathBench task_id={task_id}: missing question_type"
                )
            prompt = PROMPT_EN if language == "en" else PROMPT_CN
            yield self._register(
                row,
                split,
                task_id,
                prompt.format(question=question),
                env=self.name,
                version=self._version,
                dataset_subset=subset,
                language=language,
                question_type=row["question_type"],
            )

    def grade(self, trajs):
        graded = []
        for trajectory in trajs:
            self._row_for(trajectory)
            labels = [0] * self._n
            if trajectory.status == "finished":
                task = trajectory.task
                event = trajectory.messages[-1] if trajectory.messages else {}
                labels = event.get("labels")
                if (
                    event.get("type") != "LiveMathBenchVerifier"
                    or event.get("task_id") != task.task_id
                    or event.get("task_checksum") != task.metadata["task_checksum"]
                    or event.get("judge_revision") != REVISION
                    or event.get("n") != self._n
                    or event.get("k") != self._ks
                    or not isinstance(labels, list)
                    or len(labels) != self._n
                    or any(type(v) is not int or v not in (0, 1) for v in labels)
                ):
                    raise ValueError(
                        "LiveMathBench requires a matching final host LiveMathBenchVerifier event"
                    )
            graded.append(
                replace(
                    trajectory,
                    metrics={**trajectory.metrics, **metrics(labels, self._ks)},
                )
            )
        return tuple(graded)


__all__ = ["LiveMathBench"]
