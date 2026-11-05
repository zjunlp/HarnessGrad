"""Runtime identity binding around authored H0/checkpoint lifecycle records.

Artifact admission and measurement are mocked only in driver wiring controls;
these tests exercise publication/reuse/refusal ordering without a dataset install.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harnessgrad import dataset_runtime_identity as runtimes
from harnessgrad import policies
from harnessgrad.records import _curve_point
from harnessgrad.store.schema import check_point, check_run_meta


def identity(revision="a"):
    root = "/managed/provider/instance-" + revision
    return runtimes.create(provider={
        "dataset": "synthetic", "runtime_id": revision * 64,
        "path": root + "/venv", "python": root + "/venv/bin/python",
        "manifest_path": root + "/artifact.json", "descriptor_sha256": "d" * 64,
        "requirements_sha256": "e" * 64})


def point(runtime=None):
    return _curve_point(run_id="test", round_index=0, identity={},
        sampling={"task_ids": ["t"]}, scores=[.5], task_ids=["t"], cost={}, cumulative={},
        dataset_runtime_identity=runtime)


def test_points_and_readers_preserve_legacy_shape_and_validate_runtime_records():
    legacy = point()
    assert "dataset_runtime_identity" not in legacy
    current = point(identity())
    assert current["dataset_runtime_identity"] == identity()
    for check in (check_point, check_run_meta):
        assert check(current) == check(legacy)
        bad = copy.deepcopy(current)
        bad["dataset_runtime_identity"]["sha256"] = "0" * 64
        problems, _ = check(bad)
        assert any("dataset_runtime_identity" in item for item in problems)


def test_frozen_reference_comparability_requires_runtime_identity():
    assert policies.reference_problems(point(), point()) == []
    assert policies.reference_problems(point(identity()), point(identity())) == []
    for reference, current in ((None, identity()), (identity(), None), (identity(), identity("b"))):
        assert any("dataset_runtime_identity" in item
                   for item in policies.reference_problems(point(reference), point(current)))


@pytest.fixture
def driver_run(tmp_path, monkeypatch):
    import driver

    # The entrypoint owns environment changes; keep them local to this control.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("HG_AGENT_MODEL", "authored-lifecycle")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "")
    current = [identity()]
    monkeypatch.setattr(runtimes, "capture", lambda *args, **kwargs: copy.deepcopy(current[0]))
    admission = Mock()
    monkeypatch.setattr(runtimes, "admit", admission)

    def measurement(repo, tasks, scorable, *args, **kwargs):
        return {"per_task": {task["task_id"]: .5 for task in tasks}, "invalid": {},
                **runtimes.fields(current[0]),
                "traces": {}, "trial_traces": {}, "trials": 1}

    measured = Mock(side_effect=measurement)
    monkeypatch.setattr(driver, "evaluate", measured)
    monkeypatch.setattr(driver, "_platform_content_sha", lambda: "authored-platform")

    def run(run_id, *extra):
        monkeypatch.setattr(sys, "argv", ["driver.py", "--harness", "loop_plain",
            "--rounds", "0", "--dataset", "demo", "--sampling", "all", "--jobs", "1",
            "--improver", "none", "--seed", "authored-lifecycle", "--run-id", run_id,
            "--runs-root", str(tmp_path / "runs"), "--work-root", str(tmp_path / "work"), *extra])
        return driver.main()

    return SimpleNamespace(run=run, current=current, measured=measured,
                           admission=admission, root=tmp_path / "runs")


def read_point(run):
    return json.loads((run / "curve.jsonl").read_text().splitlines()[0])


def test_h0_records_identity_and_matching_reuse_keeps_the_original_measurement(driver_run):
    control = driver_run
    assert control.run("donor") == 0
    donor = control.root / "donor"
    for record in (json.loads((donor / "run_meta.json").read_text()),
                   json.loads((donor / "h0.json").read_text()), read_point(donor)):
        assert record["dataset_runtime_identity"] == identity()
    assert control.measured.call_count == 1
    assert control.run("consumer", "--h0-from", "donor") == 0
    assert control.measured.call_count == 1
    assert read_point(control.root / "consumer")["shared_from"]["run_id"] == "donor"
    control.admission.assert_called_with(identity())


def test_unopted_driver_keeps_legacy_records_and_h0_reuse(driver_run):
    control = driver_run
    control.current[0] = None
    assert control.run("donor") == 0
    donor = control.root / "donor"
    for record in (json.loads((donor / "run_meta.json").read_text()),
                   json.loads((donor / "h0.json").read_text()), read_point(donor)):
        assert "dataset_runtime_identity" not in record
    assert control.run("consumer", "--h0-from", "donor") == 0
    assert control.measured.call_count == 1


def test_managed_curve_fallback_keeps_the_base_identity(driver_run):
    from driver import _base_from_point

    control = driver_run
    assert control.run("donor") == 0
    (control.root / "donor/h0.json").unlink()
    assert control.run("consumer", "--h0-from", "donor") == 0
    point = read_point(control.root / "consumer")
    assert point["dataset_runtime_identity"] == identity()
    assert _base_from_point(point)["dataset_runtime_identity"] == identity()
    assert control.measured.call_count == 1


def test_removing_opt_in_cannot_reuse_or_resume_existing_identity(driver_run):
    control = driver_run
    assert control.run("donor") == 0
    metadata = control.root / "donor/run_meta.json"
    before = metadata.read_bytes()
    control.current[0] = None
    assert control.run("consumer", "--h0-from", "donor") == 2
    assert control.run("donor", "--resume") == 2
    assert control.measured.call_count == 1 and metadata.read_bytes() == before


def test_matching_runtime_resume_keeps_the_existing_h0(driver_run):
    control = driver_run
    assert control.run("donor") == 0
    directory = control.root / "donor"
    before = (directory / "h0.json").read_bytes(), (directory / "curve.jsonl").read_bytes()
    assert control.run("donor", "--resume") == 0
    assert ((directory / "h0.json").read_bytes(), (directory / "curve.jsonl").read_bytes()) == before
    assert control.measured.call_count == 1
    control.admission.assert_called_with(identity())


def test_capture_failure_cannot_rewrite_existing_run(driver_run, monkeypatch):
    control = driver_run
    assert control.run("donor") == 0
    metadata = control.root / "donor/run_meta.json"
    before = metadata.read_bytes()
    monkeypatch.setattr(runtimes, "capture", Mock(side_effect=ValueError("runtime artifact damaged")))
    assert control.run("donor", "--resume") == 2
    assert control.measured.call_count == 1 and metadata.read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "changed", "malformed"])
def test_eval_resume_checks_progress_before_metadata_or_workspace_mutation(driver_run, damage):
    control = driver_run
    assert control.run("donor") == 0
    extra = ["--side", "eval", "--from-run", "donor"]
    assert control.run("exam", *extra) == 0
    directory = control.root / "exam"
    metadata = directory / "run_meta.json"
    progress = next((directory / "progress/round-0").rglob("*.json"))
    record = json.loads(progress.read_text())
    if damage == "missing":
        record.pop("dataset_runtime_identity")
    elif damage == "changed":
        record["dataset_runtime_identity"] = identity("b")
    else:
        record["dataset_runtime_identity"]["sha256"] = "invalid"
    progress.write_text(json.dumps(record))
    before = metadata.read_bytes(), progress.read_bytes()
    workspace = Path(json.loads(before[0])["workspace"])
    sentinel = workspace / "untouched-sentinel"
    sentinel.write_text("no restaging")
    assert control.run("exam", *extra, "--resume") == 2
    assert (metadata.read_bytes(), progress.read_bytes()) == before
    assert sentinel.read_text() == "no restaging"


@pytest.mark.parametrize("record", ["run_meta.json", "h0.json", "curve.jsonl", "base"])
@pytest.mark.parametrize("change", ["changed", "missing", "damaged"])
def test_h0_reuse_refuses_each_inconsistent_record(driver_run, record, change, capsys):
    control = driver_run
    assert control.run("donor") == 0
    path = control.root / "donor" / ("h0.json" if record == "base" else record)
    body = json.loads(path.read_text())
    value = body["base"] if record == "base" else body
    if change == "changed":
        value["dataset_runtime_identity"] = identity("b")
    elif change == "missing":
        del value["dataset_runtime_identity"]
    else:
        value["dataset_runtime_identity"]["sha256"] = "0" * 64
    path.write_text(json.dumps(body) + "\n")
    original = path.read_bytes()
    assert control.run("consumer", "--h0-from", "donor") == 2
    output = capsys.readouterr()
    assert "dataset_runtime_identity" in output.out + output.err
    assert control.measured.call_count == 1 and path.read_bytes() == original
    assert not (control.root / "consumer/curve.jsonl").exists()


@pytest.mark.parametrize("side", ["train", "eval"])
@pytest.mark.parametrize("with_curve", [False, True])
def test_resume_refuses_changed_identity_before_metadata_or_checkpoint_mutation(
        driver_run, side, with_curve, capsys):
    control = driver_run
    assert control.run("donor") == 0
    extra = [] if side == "train" else ["--side", "eval", "--from-run", "donor"]
    run_id = "donor" if side == "train" else "exam"
    if side == "eval":
        assert control.run(run_id, *extra) == 0
    directory = control.root / run_id
    metadata = directory / "run_meta.json"
    curve = directory / "curve.jsonl"
    if not with_curve:
        curve.unlink()
    original = metadata.read_bytes()
    workspace = Path(json.loads(original)["workspace"])
    sentinel = workspace / "untouched-sentinel"
    sentinel.write_text("checkpoint must not be restaged")
    measured = control.measured.call_count
    control.current[0] = identity("b")
    assert control.run(run_id, *extra, "--resume", "--resume-across-platforms", "reviewed") == 2
    output = capsys.readouterr()
    assert "dataset_runtime_identity" in output.out + output.err
    assert metadata.read_bytes() == original
    assert sentinel.read_text() == "checkpoint must not be restaged"
    assert control.measured.call_count == measured


def test_resume_checks_old_points_before_rewriting_metadata(driver_run, capsys):
    control = driver_run
    assert control.run("donor") == 0
    directory = control.root / "donor"
    curve = directory / "curve.jsonl"
    old = read_point(directory)
    old["dataset_runtime_identity"] = identity("b")
    current = copy.deepcopy(old)
    current["round"] = 1
    current["dataset_runtime_identity"] = identity()
    curve.write_text(json.dumps(old) + "\n" + json.dumps(current) + "\n")
    original = (directory / "run_meta.json").read_bytes(), curve.read_bytes()
    assert control.run("donor", "--resume") == 2
    output = capsys.readouterr()
    assert "dataset_runtime_identity" in output.out + output.err
    assert ((directory / "run_meta.json").read_bytes(), curve.read_bytes()) == original


def test_train_helper_admits_runtime_before_reusing_checkpoint(driver_run, tmp_path):
    import driver

    control = driver_run
    assert control.run("donor") == 0
    directory = control.root / "donor"
    args = SimpleNamespace(rounds=1, dataset_runtime_identity=identity())
    control.admission.side_effect = ValueError("dataset_runtime_identity artifact unavailable")
    work = tmp_path / "resume-work"
    work.mkdir()
    sentinel = work / "untouched"
    sentinel.write_text("original workspace")
    before = (directory / "run_meta.json").read_bytes()
    with pytest.raises(driver.ResumeRefused, match="artifact unavailable"):
        driver._resume_train_state(args, directory, None, work)
    assert sentinel.read_text() == "original workspace"
    assert (directory / "run_meta.json").read_bytes() == before


@pytest.mark.parametrize("changed_point", [0, 1])
def test_train_helper_checks_every_checkpoint_point_before_restaging(driver_run, tmp_path, changed_point):
    import driver

    control = driver_run
    assert control.run("donor") == 0
    directory = control.root / "donor"
    points = [read_point(directory), read_point(directory)]
    points[1]["round"] = 1
    points[changed_point]["dataset_runtime_identity"] = identity("b")
    (directory / "curve.jsonl").write_text("".join(json.dumps(p) + "\n" for p in points))
    args = SimpleNamespace(rounds=2, dataset_runtime_identity=identity(),
                           resume_across_platforms="reviewed")
    work = tmp_path / "resume-work"
    work.mkdir()
    sentinel = work / "untouched"
    sentinel.write_text("original workspace")
    with pytest.raises(driver.ResumeRefused, match="dataset_runtime_identity"):
        driver._resume_train_state(args, directory, None, work)
    assert sentinel.read_text() == "original workspace"
