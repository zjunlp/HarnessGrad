"""Keep the workbook score, file boundary and trusted-input pins explicit."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.datasets import wtm_bench

CHECK = Path(__file__).resolve().parents[2] / "data/datasets/wtm_bench/verify/check.py"
SPEC = importlib.util.spec_from_file_location("wtm_check_for_test", CHECK)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def test_workbook_grade_uses_released_mode_and_rounding():
    def official(**kwargs):
        assert kwargs == {"generated_path": "output.xlsx", "ground_truth_path": "expected.xlsx",
                          "initial_path": "initial.xlsx", "mode": "values_only"}
        return SimpleNamespace(total_reward=0.999999)

    assert check.workbook_grade(SimpleNamespace(compute_reward=official), Path("output.xlsx"),
                                Path("expected.xlsx"), Path("initial.xlsx")) == (1.0, 0.999999)
    assert wtm_bench.METRIC == check.METRIC
    assert wtm_bench.SPLIT_SOURCE == "none" and wtm_bench.OFFICIAL_EVAL is None


def test_submission_requires_an_owned_workbook(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    staged = tmp_path / "staged.xlsx"
    output = tmp_path / "output.xlsx"
    assert check.stage_submission(staged) == "missing_workbook"
    output.symlink_to(CHECK)
    assert check.stage_submission(staged) == "invalid_workbook"
    assert not staged.exists()
    output.unlink()
    output.write_text("not an XLSX file")
    assert check.stage_submission(staged) == "invalid_workbook"


def test_catalog_and_private_files_refuse_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(wtm_bench, "DATASET_ROOT", tmp_path)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "catalog.json").write_text('{"rows":[]}')
    with pytest.raises(ValueError, match="catalogue digest mismatch"):
        wtm_bench.records()
    (prepared / "scorer_v2.py").write_text("changed")
    with pytest.raises(ValueError, match="prepared input digest mismatch"):
        wtm_bench._prepared_file("scorer_v2.py", wtm_bench.SCORER_SHA256)


def test_unconfigured_data_root_is_reported(monkeypatch):
    from harnessgrad import paths

    monkeypatch.setattr(paths, "_settings", lambda: {})
    monkeypatch.setattr(wtm_bench, "DATASET_ROOT", paths.DatasetPath("wtm_bench"))

    status = wtm_bench.provision_status()
    assert status["present"] is False
    assert status["root"] is None
    assert "HG_DATA_WTM_BENCH" in status["detail"]


@pytest.mark.parametrize("case,mode,score", [
    ("566", "oracle", 1), ("566", "noop", 0), ("566", "missing", 0),
    ("2939", "oracle", 1), ("2183", "oracle", 0.8), ("2284", "oracle", 0.8),
])
def test_released_workbook_platform_controls(case, mode, score):
    from tools.wtm_bench_control import run_control

    status = wtm_bench.provision_status()
    if not status["present"]:
        pytest.skip("requires prepared WTM-Bench data and execution runtime")
    result = run_control(case, mode)
    assert result["score"] == score
    assert result["scorer_input_verified"]
