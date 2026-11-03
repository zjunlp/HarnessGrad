"""Native Harvey LAB v1.2.0 tasks; user partitions and official dual all-pass.

Upstream: harveyai/harvey-labs at f93ae2ac2831e65892fea83094cfb50149bc2e59.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from flashrsi.benchmarks._git import _download_git
from flashrsi.benchmarks._native import NativeBenchmark

REVISION = "f93ae2ac2831e65892fea83094cfb50149bc2e59"
JUDGES = ("claude-sonnet-4-6", "gpt-5.5")


def _files(root):
    if (
        not root.is_dir()
        or root.is_symlink()
        or (hasattr(root, "is_junction") and root.is_junction())
    ):
        raise ValueError("Harvey LAB requires a plain documents/output directory")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Harvey LAB does not accept linked documents or outputs")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError("Harvey LAB only accepts regular files and directories")
    return files


def _record(directory, root, cache=None):
    config_path = directory / "task.json"
    if config_path.is_symlink():
        raise ValueError("Harvey LAB task.json must be a regular file")
    row = json.loads(config_path.read_text(encoding="utf-8"))
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("title"), str)
        or not row["title"].strip()
    ):
        raise ValueError(f"Harvey LAB requires a title: {directory.name}")
    if "instructions" not in row:
        raise ValueError(
            f"Harvey LAB task.json requires instructions: {directory.name}"
        )
    instructions = row["instructions"]
    if not instructions:
        path = directory / "instructions.md"
        if path.is_symlink():
            raise ValueError("Harvey LAB instructions.md must be a regular file")
        instructions = path.read_text(encoding="utf-8")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError(f"Harvey LAB requires nonempty instructions: {directory.name}")
    criteria = row.get("criteria")
    if (
        not isinstance(criteria, list)
        or not criteria
        or any(
            not isinstance(c, dict) or not {"id", "title", "match_criteria"} <= c.keys()
            for c in criteria
        )
    ):
        raise ValueError(f"Harvey LAB requires native criteria: {directory.name}")
    documents = directory / (row.get("docs_dir") or "documents")
    if documents.is_symlink() or not documents.resolve().is_relative_to(root.resolve()):
        raise ValueError("Harvey LAB docs_dir must stay inside the native task tree")
    documents = documents.resolve()
    if cache is None or documents not in cache:
        digest = hashlib.sha256()
        for path in _files(documents):
            digest.update(path.relative_to(documents).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        documents_digest = digest.hexdigest()
        if cache is not None:
            cache[documents] = documents_digest
    else:
        documents_digest = cache[documents]
    return {
        "task": row,
        "instructions": instructions,
        "documents_checksum": documents_digest,
    }, documents


class HarveyLAB(NativeBenchmark):
    name = "harvey_lab"
    description = (
        "Legal document work products graded by Harvey LAB's official rubric evaluator."
    )
    primary_metric = "all_pass_rate"
    metadata = MappingProxyType(
        {
            "source": "https://github.com/harveyai/harvey-labs.git",
            "revision": REVISION,
            "version": "1.2.0",
            "native_splits": (),
            "scoring": "official-dual-all-pass",
            "execution": "native-harness-local-workspace",
        }
    )

    def __init__(self, config):
        super().__init__(config)
        self._sources_by_id = {}
        self._artifacts_dir = None

    def _iter_tasks(self):
        data = self.config.get("data_path")
        if data is None:
            data = _download_git(self.metadata["source"], REVISION, ["tasks"])
        else:
            self.metadata = MappingProxyType(
                {
                    **self.metadata,
                    "source": "local",
                    "revision": "local",
                    "upstream_revision": REVISION,
                }
            )
        groups = data.items() if isinstance(data, Mapping) else [("unassigned", data)]
        cache = {}
        for role, paths in groups:
            if role not in {"unassigned", "train", "dev", "val", "validation", "test"}:
                raise ValueError("Harvey LAB data_path roles must name train/dev/test")
            for value in paths if isinstance(paths, list) else [paths]:
                path = Path(value).expanduser().resolve()
                if path.is_file() and path.name == "task.json":
                    path = path.parent
                if (path / "tasks").is_dir():
                    path /= "tasks"
                root = next(
                    (p for p in (path, *path.parents) if p.name == "tasks"), path
                )
                # Official firm-knowledge nests tasks/ beside its shared DMS.
                # IDs and document bounds still use the collection's tasks/.
                if (
                    root.parent.name == "firm-knowledge"
                    and root.parent.parent.name == "tasks"
                ):
                    root = root.parent.parent
                directories = (
                    [path]
                    if (path / "task.json").is_file()
                    else [p.parent for p in sorted(path.rglob("task.json"))]
                )
                for directory in directories:
                    if directory.is_symlink() or any(
                        p.is_symlink()
                        for p in directory.parents
                        if p.is_relative_to(root)
                    ):
                        raise ValueError(
                            "Harvey LAB task directories must not be links"
                        )
                    task_id = directory.relative_to(root).as_posix()
                    if task_id == "." or "/" not in task_id:
                        task_id = f"{directory.parent.name}/{directory.name}"
                    record, documents = _record(directory, root, cache)
                    self._sources_by_id[task_id] = directory, root, documents
                    row = record["task"]
                    yield self._register(
                        record,
                        "dev" if role == "val" else role,
                        task_id,
                        record["instructions"],
                        env="harvey_lab",
                        practice_area=task_id.split("/")[0],
                        work_type=row.get("work_type"),
                    )

    def _resources(self, task):
        if self._tasks_by_id.get(task.task_id) != task:
            raise ValueError("Harvey LAB requires a loaded benchmark Task")
        directory, root, _ = self._sources_by_id[task.task_id]
        record, documents = _record(directory, root)
        if record != self._rows_by_id[task.task_id]:
            raise ValueError("Harvey LAB task or documents changed after loading")
        return record["task"], documents

    def grade(self, trajs):
        graded = []
        for trajectory in trajs:
            self._row_for(trajectory)
            metrics = {
                "all_pass_rate": 0.0,
                "criterion_pass_rate": 0.0,
                "all_pass": 0.0,
            }
            if trajectory.status == "finished":
                event = trajectory.messages[-1] if trajectory.messages else {}
                task = trajectory.task
                if (
                    event.get("type") != "HarveyLABVerifier"
                    or event.get("task_id") != task.task_id
                    or event.get("task_checksum") != task.metadata["task_checksum"]
                    or event.get("scorer_revision") != REVISION
                ):
                    raise ValueError("Harvey LAB requires a final host verifier event")
                rates = event.get("metrics", {})
                if (
                    set(rates) != set(metrics)
                    or any(
                        type(v) not in (float, int) or not 0 <= v <= 1
                        for v in rates.values()
                    )
                    or rates["all_pass_rate"] not in (0, 0.5, 1)
                    or rates["all_pass"] != float(rates["all_pass_rate"] == 1)
                ):
                    raise ValueError("Invalid Harvey LAB official scores")
                metrics = dict(rates)
            graded.append(
                replace(trajectory, metrics={**trajectory.metrics, **metrics})
            )
        return tuple(graded)


__all__ = ["HarveyLAB"]
