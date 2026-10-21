"""Synthetic task directories exercise the commands the public QA goals advertise."""
from __future__ import annotations

import importlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from data.scaffolds.qa import base


def _stage(entries: list[dict], workdir: Path) -> None:
    for entry in entries:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"], encoding="utf-8")
        else:
            shutil.copyfile(entry["from"], target)


@pytest.mark.parametrize(
    "dataset",
    ["gsm8k", "triviaqa", "strategyqa", "sportsunderstanding", "physicsquestions", "bfcl_v3"],
)
def test_advertised_submit_command_runs_from_staged_task(dataset, tmp_path):
    # Import scoring/prompt code without loading any released dataset records.
    with patch.object(base.QASpec, "records", return_value=[]):
        module = importlib.import_module(f"data.datasets.{dataset}")
    if dataset == "bfcl_v3":
        goal = module._goal({"user_request": "A fabricated request.", "functions_json": "[]"})
    else:
        goal = module._goal("A fabricated question.", {"answer": "unused"})
    advertised = re.search(r"python3\s+(\S+)\s+submit\b", goal)
    assert advertised, goal
    _stage(module._SPEC.setup({})["files"], tmp_path)

    proc = subprocess.run(
        [sys.executable, advertised.group(1), "submit", "fabricated answer"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "fabricated answer\n"


def test_spreadsheet_staged_qa_can_submit_workbook_filename(tmp_path):
    import data.datasets.spreadsheetbench as dataset

    initial = tmp_path / "synthetic-input.xlsx"
    initial.write_bytes(b"synthetic input artifact; this test does not open workbooks")
    work = tmp_path / "task"
    work.mkdir()
    _stage(dataset._setup({"init_path": initial})["files"], work)

    proc = subprocess.run(
        [sys.executable, "qa/bin/qa", "submit", "result.xlsx"],
        cwd=work, capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (work / "answer.txt").read_text(encoding="utf-8") == "result.xlsx\n"
    assert (work / "workbook.xlsx").read_bytes() == initial.read_bytes()
