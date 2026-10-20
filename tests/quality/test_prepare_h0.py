"""The H0 pass: one baseline per configuration, and a refusal to publish a holey one.

`driver.py --rounds N` measures round 0 inside every run, so a batch that starts N runs has N
baselines unless someone shares them. `tools/prepare_h0.py` is the missing step: measure once,
hand the run id to every study run. These tests pin the two things that make it trustworthy --
what counts as "the same configuration", and what is allowed to be called an H0.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("prepare_h0", ROOT / "tools" / "prepare_h0.py")
prepare_h0 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_h0)


def _args(tmp_path: pathlib.Path, ids: list[str] | None = None, **over):
    ids_file = None
    if ids is not None:
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("\n".join(ids) + "\n")
    base = {"dataset": "appworld", "side": "train", "study_ids": ids_file,
            "model": "qwen3.5-9b-local", "endpoint": "http://127.0.0.1:8100/v1", "trials": 1}
    base.update(over)
    return type("Args", (), base)


def test_the_same_ids_in_a_different_order_are_the_same_configuration(tmp_path):
    a = prepare_h0.configuration(_args(tmp_path, ["t2", "t1"]))
    b = prepare_h0.configuration(_args(tmp_path, ["t1", "t2"]))
    assert a == b, "the study set is a set: file order is not part of the measurement"


def test_a_different_study_set_or_model_is_a_different_h0(tmp_path):
    a = prepare_h0.configuration(_args(tmp_path, ["t1"]))
    b = prepare_h0.configuration(_args(tmp_path, ["t1", "t2"]))
    c = prepare_h0.configuration(_args(tmp_path, ["t1"], model="other-model"))
    assert len({json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True),
                json.dumps(c, sort_keys=True)}) == 3


def _write_run(root: pathlib.Path, name: str, *, per_task: dict, invalid=None, sha="abc",
               config=None) -> pathlib.Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "h0.json").write_text(json.dumps({
        "h0_sha": sha,
        "base": {"per_task": per_task, "invalid": invalid or {}, "score": 0.5,
                 "harness_failed": None}}))
    (run / "run_meta.json").write_text(json.dumps({"h0_configuration": config}))
    return run


def test_a_complete_baseline_is_accepted(tmp_path):
    run = _write_run(tmp_path, "h0-appworld-1", per_task={"t1": 0.0, "t2": 1.0})
    summary = prepare_h0.verify(run, None, 2)
    assert summary == {"score": 0.5, "n_tasks": 2, "h0_sha": "abc"}


@pytest.mark.parametrize("kwargs, needle", [
    ({"per_task": {"t1": 0.0}, "invalid": {"t2": {"stage": "admission"}}, "n": 2}, "invalid"),
    ({"per_task": {"t1": 0.0}, "n": 2}, "1 tasks measured, the study set has 2"),
    ({"per_task": {}, "n": 2}, "no per-task verdicts"),
    ({"per_task": {"t1": 0.0, "t2": 1.0}, "sha": "", "n": 2}, "no h0_sha recorded"),
])
def test_a_holey_baseline_is_refused_not_published(tmp_path, kwargs, needle):
    n = kwargs.pop("n")
    run = _write_run(tmp_path, "h0-appworld-2", **kwargs)
    with pytest.raises(SystemExit) as exc:
        prepare_h0.verify(run, None, n)
    assert needle in str(exc.value)


def test_an_existing_baseline_is_reused_for_the_same_configuration(tmp_path):
    config = {"dataset": "appworld", "study_ids_sha": "x", "n_study_ids": 2}
    run = _write_run(tmp_path, "h0-appworld-3", per_task={"t1": 0.0, "t2": 1.0},
                     config=config)
    found = prepare_h0.find_existing(config, tmp_path)
    assert found is not None and found[0] == run


def test_a_different_configuration_is_not_reused(tmp_path):
    _write_run(tmp_path, "h0-appworld-4", per_task={"t1": 0.0, "t2": 1.0},
               config={"dataset": "appworld", "study_ids_sha": "other"})
    assert prepare_h0.find_existing({"dataset": "appworld", "study_ids_sha": "x"}, tmp_path) is None


def test_a_missing_study_ids_file_is_refused(tmp_path):
    with pytest.raises(SystemExit) as exc:
        prepare_h0.main(["--dataset", "appworld", "--study-ids", str(tmp_path / "missing.txt"),
                         "--runs-root", str(tmp_path / "runs"), "--dry-run"])
    assert "no such study-ids file" in str(exc.value)


def test_dry_run_measures_the_baseline_and_nothing_else(tmp_path, capsys):
    ids = tmp_path / "ids.txt"
    ids.write_text("t1\nt2\n")
    code = prepare_h0.main(["--dataset", "appworld", "--study-ids", str(ids),
                            "--runs-root", str(tmp_path / "runs"), "--dry-run"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "--rounds 0" in printed and "--improver none" in printed
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").glob("h0-*"))
