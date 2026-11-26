"""WTM-Bench curated Level 5 tasks scored with the released workbook cell grade."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from data.scaffolds.declarations import TaskDeclarations
from data.scaffolds.native_harbor import safe_file
from data.scaffolds.scorer_input import bind as bind_scorer_input, source_digest
from data.scaffolds.spreadsheet_runtime.actor import environment
from harnessgrad.paths import dataset_root

KIND = "dataset"
VERIFIER_KIND = "command"
ORACLE_SOURCE = "released"
OFFICIAL_EVAL = None
SPLIT = None
SPLIT_SOURCE = "none"
DATASET_ROOT = dataset_root("wtm_bench")
ASSETS = Path(__file__).resolve().with_suffix("")
REVISION = "83d45ae601f3241ac7246613c67b765e48f93c06"
ARCHIVE_SHA256 = "5642966efd2ac4f35e6497bd81365c7d874f19813c85643011581ab62e8b9220"
SCORER_SHA256 = "4f767a9f00899bbea581eb161d5a535e6ec3282f3183df2afbe18c4d366aee84"
CATALOG_SHA256 = "28e23a8d3b4152a146802e3bf5d372e44d02c240cb0d9dacb2994a95ba898a6c"
TASK_COUNT = 150
METRIC = "wtm_bench.workbook_cell_grade"


def records():
    root = DATASET_ROOT / "prepared"
    raw = safe_file(root, "catalog.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
        raise ValueError("WTM-Bench prepared catalogue digest mismatch")
    return json.loads(raw)["rows"]


def _prepared_file(relative, expected):
    path = safe_file(DATASET_ROOT / "prepared", relative)
    if source_digest(path) != expected:
        raise ValueError("WTM-Bench prepared input digest mismatch")
    return path


def load():
    rows = records()
    tasks = [
        {"task_id": row["task_id"],
         "goal": row["prompt"] + "\n\nOpen input.xlsx in the working directory. "
                 "Use the task's Python and openpyxl to edit the workbook. "
                 "Save the completed workbook as output.xlsx in the working directory.",
         "submission": {"kind": "files", "paths": ["output.xlsx"]}}
        for row in rows
    ]
    return tasks, {row["task_id"]: METRIC for row in rows}


def _setup(row):
    path = _prepared_file(row["initial"], row["initial_sha256"])
    return {"files": [{"path": "input.xlsx", "from": str(path)}]}


def _verify(row):
    from harnessgrad.datasets.runtime import bind, resolve_execution

    runtime = bind(resolve_execution("wtm_bench"), "verifier")
    sources = {
        "_wtm_check.py": ASSETS / "verify/check.py",
        "_wtm_scorer.py": _prepared_file("scorer_v2.py", SCORER_SHA256),
        "_wtm_initial.xlsx": _prepared_file(row["initial"], row["initial_sha256"]),
        "_wtm_expected.xlsx": _prepared_file(row["expected"], row["expected_sha256"]),
    }
    spec = {
        "kind": "command",
        "python_runtime": runtime,
        "runtime_paths": list(runtime["runtime_paths"]),
        "argv": [runtime["python"], "-I", "-B", "_wtm_check.py"],
        "inputs": [{"path": name, "from": str(path)} for name, path in sources.items()],
        "reward_file": "/verifier/reward.txt",
        "evidence_file": "/verifier/evidence.json",
        "timeout_s": 180,
        "result_contract": {"version": 1, "score_range": [0, 1], "require_exit_zero": True},
    }
    return bind_scorer_input(spec, row["task_id"], list(sources))


def __getattr__(name):
    if name == "SETUP":
        return TaskDeclarations(records(), _setup)
    if name == "VERIFY":
        return TaskDeclarations(records(), _verify)
    if name == "ENV":
        return TaskDeclarations(records(), lambda row: environment("wtm_bench"))
    raise AttributeError(name)


def downloads():
    return [{
        "kind": "http",
        "name": "Microsoft WTM-Bench curated workbook pairs and native grader",
        "version": REVISION,
        "url": f"https://codeload.github.com/microsoft/WTM-Bench/tar.gz/{REVISION}",
        "sha256": ARCHIVE_SHA256,
        "size": "23,404,688 byte archive",
        "dst": "source.tar.gz",
        "note": "Prepare the 150 curated Level 5 evaluation tasks, and prepare the managed Python execution runtime separately.",
    }]


def provision_status():
    from harnessgrad.datasets.runtime import check_execution

    status = {"root": None, "execution_runtime": check_execution("wtm_bench")}
    try:
        status["root"] = str(DATASET_ROOT)
        rows = records()
        _prepared_file("scorer_v2.py", SCORER_SHA256)
        for row in rows:
            _prepared_file(row["initial"], row["initial_sha256"])
            _prepared_file(row["expected"], row["expected_sha256"])
    except (OSError, ValueError) as exc:
        return {**status, "present": False, "detail": str(exc)}
    return {**status, "present": status["execution_runtime"]["present"],
            "tasks": len(rows), "metric": METRIC}
