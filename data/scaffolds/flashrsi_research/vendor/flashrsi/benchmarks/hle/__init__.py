"""Native HLE text-only questions and host-added official judge verdicts."""

from dataclasses import replace
from types import MappingProxyType

from flashrsi.benchmarks._native import NativeBenchmark

_TEXT_COLUMNS = ("id", "question", "image", "answer", "category", "answer_type")


class HLE(NativeBenchmark):
    name = "hle"
    description = (
        "Humanity's Last Exam text-only subset, using the official judge protocol."
    )
    primary_metric = "accuracy"
    metadata = MappingProxyType(
        {
            "source": "cais/hle",
            "revision": "5a81a4c7271a2a2a312b9a690f0c2fde837e4c29",
            "subset": "text-only",
            "judge_revision": "22ed3074b1e7b134bcbc09028d0ba320839b0655",
        }
    )

    def _source_rows(self):
        if self.config.get("data_path") is not None:
            yield from super()._source_rows()
            return
        from datasets import load_dataset

        # Official shards also contain Image features for previews and
        # rationales. Project before iteration so text tasks never decode them.
        dataset = load_dataset(
            self.metadata["source"],
            revision=self.metadata["revision"],
            streaming=True,
            columns=list(_TEXT_COLUMNS),
        )
        for split, rows in dataset.items():
            for row in rows:
                yield split, row

    def _iter_tasks(self):
        from flashrsi.integrations._hle_judge import SYSTEM_PROMPT

        for split, row in self._source_rows():
            row = {key: row[key] for key in _TEXT_COLUMNS if key in row}
            image = row.get("image", "")
            if image is not None and not isinstance(image, str):
                raise ValueError("HLE image must be a URL/base64 string or empty")
            if image and image.strip():
                continue
            answer = row.get("answer")
            question = row.get("question")
            if (
                not isinstance(answer, str)
                or not answer.strip()
                or not isinstance(question, str)
                or not question.strip()
            ):
                raise ValueError("HLE requires native question and answer fields")
            yield self._register(
                row,
                split,
                row.get("id"),
                question + "\n\n" + SYSTEM_PROMPT,
                env="hle",
                category=row.get("category"),
                answer_type=row.get("answer_type"),
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
                    event.get("type") != "HLEVerifier"
                    or event.get("task_id") != task.task_id
                    or event.get("task_checksum") != task.metadata["task_checksum"]
                    or type(event.get("correct")) is not bool
                ):
                    raise ValueError(
                        "HLE requires a matching final host HLEVerifier event"
                    )
                score = float(event["correct"])
            graded.append(
                replace(trajectory, metrics={**trajectory.metrics, "accuracy": score})
            )
        return tuple(graded)


__all__ = ["HLE"]
