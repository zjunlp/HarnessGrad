"""Focused regressions for SpreadsheetBench's materialized oracle submission."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

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


def test_materialized_oracle_submits_and_scores_one(control_record, tmp_path, capfd, monkeypatch):
    runtime_root = os.environ.get("HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT")
    if not runtime_root or not os.environ.get("HG_SPREADSHEET_NATIVE_MANIFEST"):
        pytest.skip("explicit external Python artifact and prepared native manifest required")
    monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", runtime_root)
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


def test_authored_formula_control_preserves_input_and_scores_private_copy(tmp_path):
    from harnessgrad import dataset_runtime
    runtime_root = os.environ.get("HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT")
    native_manifest = os.environ.get("HG_SPREADSHEET_NATIVE_MANIFEST")
    if not runtime_root or not native_manifest:
        pytest.skip("explicit external Python artifact and prepared native manifest required")
    artifact = dataset_runtime.resolve_execution("spreadsheetbench", root=runtime_root)
    unshare = shutil.which("unshare")
    ip = shutil.which("ip", path=os.pathsep.join((os.environ.get("PATH", os.defpath),
                                                 "/usr/sbin", "/sbin")))
    if not unshare or not ip or not Path("/usr/bin/soffice").is_file():
        pytest.skip("unshare, ip and /usr/bin/soffice are required for the authored control")
    if not Path("/proc/self/ns/net").exists():
        pytest.skip("Linux network namespaces are required for the authored control")
    home, temporary = tmp_path / "home", tmp_path / "tmp"
    home.mkdir()
    temporary.mkdir()
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(temporary),
           "SPREADRT_PARENT_NETNS": os.readlink("/proc/self/ns/net")}
    namespace = [unshare, "--user", "--map-root-user", "--net"]
    probe = subprocess.run([*namespace, ip, "link", "set", "lo", "up"],
                           env=env, capture_output=True, text=True, timeout=10)
    if probe.returncode:
        pytest.skip("user/network namespace setup unavailable: " + probe.stderr.strip())
    setup = ("import os,subprocess,sys; "
             "subprocess.run([sys.argv[1],'link','set','lo','up'],check=True); "
             "os.execv(sys.executable,[sys.executable,*sys.argv[2:]])")
    work = tmp_path / "authored-formulas"
    control = ROOT / "tests/fixtures/hg_spreadrt/synthetic_control.py"
    result = subprocess.run([*namespace, artifact["python"], "-I", "-B", "-c", setup, ip,
                             str(control), str(work), native_manifest], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "synthetic_control=PASSED" in result.stdout
    evidence = json.loads((work / "evidence.json").read_text())
    assert evidence["measured"] is True and evidence["recalculated"] is True
    assert evidence["score"] == 1.0 and evidence["scorer_input"]["verified"] is True
    assert (work / "reward.txt").read_text() == "1\n"
    workbook = openpyxl.load_workbook(work / "result.xlsx", data_only=True)
    try:
        assert [workbook.active["B1"].value, workbook.active["B2"].value] == [999, 888]
    finally:
        workbook.close()
