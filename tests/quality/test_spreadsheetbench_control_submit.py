"""Focused regressions for SpreadsheetBench's materialized oracle submission."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

import openpyxl
import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def control_record(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "spreadsheetbench_submit_control",
        ROOT / "data" / "datasets" / "spreadsheetbench" / "control.py",
    )
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)

    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "initial"
    initial = tmp_path / "initial.xlsx"
    workbook.save(initial)
    workbook.active["A1"] = "solved"
    golden = tmp_path / "reference.xlsx"
    workbook.save(golden)
    workbook.close()
    record = {
        "task_id": "synthetic-submit-control",
        "instruction_type": "Cell-Level Manipulation",
        "answer_position": "Sheet!A1",
        "init_path": initial,
        "golden_path": golden,
    }
    monkeypatch.setattr(control.sb, "_RECORDS", [record])
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("HG_QA_ANSWER", raising=False)
    return control, record


def test_materialized_oracle_submits_and_scores_one(control_record, tmp_path, capfd):
    control, record = control_record
    workdir = tmp_path / "work"

    assert control.run_local(control.sb, record, oracle=True, workdir=workdir) == 0
    assert (workdir / "answer.txt").read_text().strip() == control.sb.RESULT_WORKBOOK
    assert (workdir / control.sb.RESULT_WORKBOOK).read_bytes() == record["golden_path"].read_bytes()
    output = capfd.readouterr()
    assert '"measured": true' in output.out
    assert '"reward": 1.0' in output.out


def test_failed_submission_prints_both_streams(control_record, tmp_path, capfd):
    control, record = control_record
    workdir = tmp_path / "work"
    control.materialize(record, workdir)
    (workdir / "qa" / "bin" / "qa").write_text(
        "import sys\n"
        "print('submission stdout detail', flush=True)\n"
        "print('submission stderr detail', file=sys.stderr, flush=True)\n"
        "raise SystemExit(7)\n"
    )

    with pytest.raises(subprocess.CalledProcessError) as failure:
        control.solve(record, workdir)

    assert failure.value.returncode == 7
    output = capfd.readouterr()
    assert "submission stdout detail" in output.out
    assert "submission stderr detail" in output.err
