"""Real offline Python artifacts through ordinary source/checkpoint lifecycle.

Only task measurement and method replies are authored doubles. Preparation,
source staging, H0 copies, checkpoints and resume admission use production code.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eval.ckpt.source_tree import copy_harness
from eval.observe.tracing import _write_progress
from harnessgrad import candidate_runtime
from harnessgrad import python_harness_runtime, verification_context


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session(tmp_path, monkeypatch):
    if shutil.which("uv") is None or shutil.which("bwrap") is None:
        pytest.skip("offline managed candidate lifecycle needs uv and bubblewrap")
    import driver
    from harnessgrad.loops import eval_side, mode_a

    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("HG_AGENT_MODEL", "authored-lifecycle-control")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "")
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HG_HARNESS_RUNTIME_ROOT", str(tmp_path / "runtime-store"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    source = tmp_path / "source"
    shutil.copytree(ROOT / "tests/fixtures/base_harness/loop_plain", source)
    manifest = json.loads((source / "harness.json").read_text())
    manifest.update(name="managed-control", runtime={"kind": "python",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "requirements": "requirements.lock"})
    (source / "harness.json").write_text(json.dumps(manifest))
    (source / "requirements.lock").write_text("# initial empty dependencies\n")
    original_harness = driver._harness_by_name
    monkeypatch.setattr(driver, "_harness_by_name", lambda name: source if name == "managed-control" else original_harness(name))
    monkeypatch.setattr(driver, "_platform_content_sha", lambda: "authored-platform")
    measured = []
    pause = [None]

    def evaluate(repo, tasks, scorable, *args, **options):
        value = options.get("harness_runtime_identity")
        assert value is not None
        candidate_runtime.admit(repo, value)
        context = verification_context.project(options["verification_context"],
                                               [task["task_id"] for task in tasks])
        assert context == verification_context.capture(
            tasks, scorable, options.get("setups"), options.get("verifiers"),
            options.get("envs"), sandbox=options["sandbox"])
        scores = {}
        for task in tasks:
            tid = task["task_id"]
            if pause[0] == tid:
                raise KeyboardInterrupt("authored partial evaluation")
            measured.append((tid, copy.deepcopy(value)))
            scores[tid] = 1.0
            result = {"task_id": tid, "exit_code": 0, "trace": "", "stderr": "",
                      "verdict": {"kind": "answer", "score": 1, "passed": True,
                                  "scorer_isolation_class": "actor-workdir"}}
            _write_progress(options.get("progress_dir"), options.get("round_no", 0), tid,
                            [result], None, harness_runtime_identity=value,
                            verification_context=verification_context.project(context, [tid]))
        return {"per_task": scores, "invalid": {}, "trials": 1,
                "per_task_scorer_isolation": {tid: "actor-workdir" for tid in scores},
                "traces": {}, "trial_traces": {}, **candidate_runtime.fields(value),
                **verification_context.fields(context)}

    measurement = Mock(side_effect=evaluate)
    monkeypatch.setattr(driver, "evaluate", measurement)
    monkeypatch.setattr(eval_side, "evaluate", measurement)
    monkeypatch.setattr(mode_a, "evaluate", measurement)
    prepare = Mock(wraps=candidate_runtime.prepare)
    monkeypatch.setattr(candidate_runtime, "prepare", prepare)
    candidate_index = [0]
    edits = ["# changed empty dependencies\n"]

    def submit(work, _argv, request, *_args, **_kwargs):
        target = tmp_path / f"candidate-{candidate_index[0]}"
        candidate_index[0] += 1
        copy_harness(work, target, ignore=shutil.ignore_patterns(".git", "_harnessgrad"))
        edit = edits[0]
        if edit in {"drop-runtime", "repair-runtime"}:
            changed = json.loads((target / "harness.json").read_text())
            if edit == "drop-runtime":
                changed.pop("runtime")
            else:
                changed["runtime"] = copy.deepcopy(manifest["runtime"])
                (target / "requirements.lock").write_text("# repaired empty dependencies\n")
            (target / "harness.json").write_text(json.dumps(changed))
        elif edit == "ignored-source":
            (target / "untracked-helper.py").write_text("VALUE = 'edited owned source'\n")
        elif edit == "empty-directory":
            (target / "assets").mkdir()
        elif edit is not None:
            (target / "requirements.lock").write_text(edit)
        Path(request["trajectory_out"]).write_text(json.dumps({
            "steps": [{"harness_dir": str(target), "label": "authored dependency edit"}]}))
        return {"changed": True}

    monkeypatch.setattr(mode_a, "_call_method", submit)

    def run(run_id, rounds=0, *extra, tasks="t01"):
        monkeypatch.setattr(sys, "argv", ["driver.py", "--harness", "managed-control",
            "--dataset", "demo", "--sampling", "all", "--jobs", "1", "--trials", "1",
            "--rounds", str(rounds), "--run-id", run_id, "--seed", "authored-control",
            "--tasks", tasks, "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / "work"), "--no-summary",
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}", *extra])
        return driver.main()

    try:
        yield SimpleNamespace(run=run, measured=measured, measurement=measurement,
            prepare=prepare, source=source, root=tmp_path / "runs", edits=edits, pause=pause)
    finally:
        store = tmp_path / "runtime-store"
        if store.exists():
            python_harness_runtime.remove_instance(store)


def curve(path):
    return [json.loads(line) for line in (path / "curve.jsonl").read_text().splitlines() if line.strip()]


def test_initial_preparation_occurs_once_for_multiple_tasks_and_cost_is_recorded(session):
    assert session.run("first", tasks="t01,t02") == 0
    run = session.root / "first"
    point = curve(run)[0]
    meta = json.loads((run / "run_meta.json").read_text())
    payload = json.loads((run / "h0.json").read_text())
    value = point["harness_runtime_identity"]
    assert meta["harness_runtime_identity"] == payload["harness_runtime_identity"] == value
    assert payload["base"]["harness_runtime_identity"] == value
    context = meta["verification_context"]
    assert point["verification_context"] == payload["verification_context"] == context
    assert payload["base"]["verification_context"] == context
    for tid in ("t01", "t02"):
        progress = json.loads((run / f"progress/round-0/{tid}.json").read_text())
        assert progress["verification_context"] == verification_context.project(context, [tid])
    assert session.prepare.call_count == 1 and len(session.measured) == 2
    receipts = list((run / "harness-preparation/round-0").glob("attempt-*.json"))
    assert len(receipts) == 1
    seconds = point["harness_preparation"]["seconds"]
    assert seconds >= 0
    assert point["cost"]["harness_preparation_wall_clock_s"] == seconds
    assert point["cumulative"]["harness_preparation_wall_clock_s"] == seconds
    assert point["cumulative"]["wall_clock_s"] >= seconds


def test_h0_reuses_its_exact_artifact_without_remeasurement(session):
    assert session.run("donor") == 0
    donor = curve(session.root / "donor")[0]
    before = len(session.measured)
    assert session.run("consumer", 0, "--h0-from", "donor") == 0
    current = curve(session.root / "consumer")[0]
    assert current["harness_runtime_identity"] == donor["harness_runtime_identity"]
    assert current["shared_from"]["run_id"] == "donor"
    assert current["verification_context"] == donor["verification_context"]
    assert len(session.measured) == before
    assert session.prepare.call_args.kwargs["binding"] == donor["harness_runtime_identity"]


@pytest.mark.parametrize("record", ["metadata", "point", "payload", "base"])
@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_h0_refuses_inconsistent_actor_identity_without_changing_donor(session, record, damage):
    assert session.run("donor") == 0
    directory = session.root / "donor"
    path = directory / {"metadata": "run_meta.json", "point": "curve.jsonl",
                        "payload": "h0.json", "base": "h0.json"}[record]
    body = json.loads(path.read_text())
    target = body["base"] if record == "base" else body
    if damage == "missing":
        target.pop("harness_runtime_identity")
    else:
        target["harness_runtime_identity"] = {"kind": "broken"}
    path.write_text(json.dumps(body) + "\n")
    before = path.read_bytes()
    assert session.run("consumer", 0, "--h0-from", "donor") == 2
    assert path.read_bytes() == before and len(session.measured) == 1


def test_dependency_edits_are_measured_and_resume_keeps_each_checkpoints_binding(session):
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    before = curve(directory)
    assert len(before) == 2
    assert before[0]["harness_runtime_identity"] != before[1]["harness_runtime_identity"]
    initial = before[0]["harness_runtime_identity"]
    session.edits[0] = "# third empty dependency declaration\n"
    # Restored checkpoints, not this mutable original, are the resume source.
    (session.source / "agent.py").write_text("raise RuntimeError('changed original')\n")
    assert session.run("study", 2, "--resume") == 0
    after = curve(directory)
    assert [point["round"] for point in after] == [0, 1, 2]
    assert after[:2] == before
    assert after[2]["harness_runtime_identity"] != after[1]["harness_runtime_identity"]
    meta = json.loads((directory / "run_meta.json").read_text())
    assert meta["harness_runtime_identity"] == initial
    assert len(session.measured) == 3


def test_managed_last_checkpoint_can_resume_from_its_portable_snapshot(session):
    from harnessgrad import snapshot
    from eval.ckpt.source_tree import PYTHON_GENERATED

    assert session.run("study", 1) == 0
    directory = session.root / "study"
    before = curve(directory)
    stored = [snapshot.load(snapshot.path_for(directory, number)) for number in (0, 1)]
    assert stored[0]["parent_id"] is None
    assert stored[1]["parent_id"] == stored[0]["id"]
    for number, artifact in enumerate(stored):
        assert artifact["harness_sha"] == before[number]["identity"]["harness_sha"]
        assert all(not (set(Path(name).parts) & PYTHON_GENERATED)
                   and not name.endswith((".pyc", ".pyo")) for name in artifact["files"])
        assert artifact["files"] == snapshot.collect(directory / f"state_after_round{number}")
    metadata = json.loads((directory / "run_meta.json").read_text())
    shutil.rmtree(directory / "state_after_round0")
    shutil.rmtree(directory / "state_after_round1")
    session.edits[0] = "# portable continuation dependencies\n"
    assert session.run("study", 2, "--resume") == 0
    after = curve(directory)
    assert after[:2] == before and [point["round"] for point in after] == [0, 1, 2]
    resumed = snapshot.load(snapshot.path_for(directory, 2))
    assert resumed["parent_id"] == stored[1]["id"]
    assert all(not (set(Path(name).parts) & PYTHON_GENERATED)
               for name in resumed["files"])
    assert json.loads((directory / "run_meta.json").read_text())["harness_runtime_identity"] == metadata["harness_runtime_identity"]
    assert len(session.measured) == 3


@pytest.mark.parametrize("damage", ["missing", "changed", "malformed"])
def test_resume_refuses_identity_damage_before_metadata_or_workspace_mutation(session, damage):
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    points = curve(directory)
    if damage == "missing":
        points[-1].pop("harness_runtime_identity")
    elif damage == "changed":
        points[-1]["harness_runtime_identity"] = points[0]["harness_runtime_identity"]
    else:
        points[-1]["harness_runtime_identity"] = {"kind": "broken"}
    (directory / "curve.jsonl").write_text("\n".join(json.dumps(point) for point in points) + "\n")
    metadata = directory / "run_meta.json"
    before = metadata.read_bytes(), (directory / "curve.jsonl").read_bytes()
    workspace = Path(json.loads(before[0])["workspace"])
    sentinel = workspace / "must-survive-refusal"
    sentinel.write_text("preserved")
    assert session.run("study", 2, "--resume") == 2
    assert before == (metadata.read_bytes(), (directory / "curve.jsonl").read_bytes())
    assert sentinel.read_text() == "preserved"


def test_preparation_failure_retains_source_and_refusal_without_an_actor_measurement(session):
    (session.source / "requirements.lock").write_text("uncached-fixture-package==0.0.1\n")
    assert session.run("unprepared") == 2
    directory = session.root / "unprepared"
    assert (directory / "state_after_round0/requirements.lock").is_file()
    assert not (directory / "curve.jsonl").exists() and not session.measured
    receipt = json.loads(next((directory / "harness-preparation/round-0").glob("attempt-*.json")).read_text())
    assert receipt["receipt"]["status"] in {"failed", "invalid", "unavailable"}


def test_initial_empty_source_directory_is_retained_as_an_unmeasured_refusal(session):
    (session.source / "assets").mkdir()
    assert session.run("empty-source") == 2
    directory = session.root / "empty-source"
    assert (directory / "state_after_round0/assets").is_dir()
    assert not (directory / "curve.jsonl").exists()
    assert not session.measured and session.prepare.call_count == 0
    receipt = json.loads(next((directory / "harness-preparation/round-0").glob("attempt-*.json")).read_text())
    assert "empty source directory" in receipt["receipt"]["error"]


def test_candidate_empty_source_directory_is_unmeasured_before_runtime_preparation(session):
    session.edits[0] = "empty-directory"
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    points = curve(directory)
    assert points[1]["unmeasured"] and not points[1]["per_task"]
    assert any("empty source directory" in problem for problem in points[1]["candidate_problems"])
    assert len(session.measured) == 1 and session.prepare.call_count == 1
    assert (directory / "state_after_round1/assets").is_dir()


@pytest.mark.parametrize("damage", ["python-version", "requirements-path", "extra-option"])
def test_malformed_named_managed_source_is_a_retained_refusal(session, damage):
    path = session.source / "harness.json"
    manifest = json.loads(path.read_text())
    if damage == "python-version":
        manifest["runtime"]["python"] = "not-a-python-version"
    elif damage == "requirements-path":
        manifest["runtime"]["requirements"] = "../outside.lock"
    else:
        manifest["runtime"]["arbitrary-command"] = "must not execute"
    path.write_text(json.dumps(manifest))
    assert session.run("malformed") == 2
    directory = session.root / "malformed"
    assert (directory / "state_after_round0/harness.json").read_bytes() == path.read_bytes()
    assert not session.measured and session.prepare.call_count == 0
    assert not (directory / "curve.jsonl").exists()
    receipt = json.loads(next((directory / "harness-preparation/round-0").glob("attempt-*.json")).read_text())
    assert receipt["receipt"]["status"] == "invalid"
    assert receipt["receipt"]["seconds"] == 0


def test_managed_candidate_cannot_drop_its_runtime_and_can_repair_after_resume(session):
    session.edits[0] = "drop-runtime"
    assert session.run("study", 1) == 0
    points = curve(session.root / "study")
    assert points[-1]["unmeasured"] and not points[-1]["measured_by_platform"]
    assert not points[-1]["per_task"] and points[-1]["n_scored"] == 0
    assert points[-1]["cost"]["evaluation_trials"] == 0
    assert points[-1]["verification_context"] == verification_context.project(
        points[0]["verification_context"], points[-1]["sampling"]["task_ids"])
    assert len(session.measured) == 1
    # Repair uses a valid new candidate derived from the recorded H0.
    session.edits[0] = "repair-runtime"
    assert session.run("study", 2, "--resume") == 0
    repaired = curve(session.root / "study")
    assert len(repaired) == 3 and repaired[-1]["measured_by_platform"]
    assert repaired[:2] == points
    assert "reused_from" not in repaired[-1]
    assert len(session.measured) == 2


def test_unchanged_candidate_reuses_the_exact_prior_binding(session):
    session.edits[0] = None
    assert session.run("study", 1, "--reuse-identical") == 0
    points = curve(session.root / "study")
    assert points[1]["reused_from"] == 0
    assert points[1]["harness_runtime_identity"] == points[0]["harness_runtime_identity"]
    assert session.prepare.call_args.kwargs["binding"] == points[0]["harness_runtime_identity"]
    assert len(session.measured) == 1


def test_changed_gitignored_owned_source_gets_a_new_binding_and_measurement(session):
    (session.source / ".gitignore").write_text("untracked-helper.py\n")
    (session.source / "untracked-helper.py").write_text("VALUE = 'original owned source'\n")
    session.edits[0] = "ignored-source"
    assert session.run("study", 1, "--reuse-identical") == 0
    points = curve(session.root / "study")
    assert points[0]["identity"]["harness_sha"] == points[1]["identity"]["harness_sha"]
    assert points[0]["harness_runtime_identity"] != points[1]["harness_runtime_identity"]
    assert points[0]["harness_runtime_identity"]["artifact"] == points[1]["harness_runtime_identity"]["artifact"]
    assert len(session.measured) == 2 and "reused_from" not in points[1]


def test_eval_uses_the_selected_train_checkpoint_not_the_initial_environment(session):
    assert session.run("study", 1) == 0
    trained = curve(session.root / "study")
    assert session.run("exam", 0, "--side", "eval", "--from-run", "study",
                       "--from-round", "1", tasks="t05,t06") == 0
    point = curve(session.root / "exam")[0]
    assert point["harness_runtime_identity"] == trained[1]["harness_runtime_identity"]
    assert point["harness_runtime_identity"] != trained[0]["harness_runtime_identity"]
    assert session.prepare.call_args.kwargs["binding"] == trained[1]["harness_runtime_identity"]


def test_eval_uses_original_artifact_after_a_new_publication_for_the_same_checkpoint(session):
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    original = curve(directory)[1]["harness_runtime_identity"]
    source = directory / "state_after_round1"
    later = python_harness_runtime.prepare(source, force=True)["identity"]
    assert later["artifact"]["runtime_id"] != original["artifact"]["runtime_id"]
    assert later["artifact"]["dependency_key"] == original["artifact"]["dependency_key"]
    assert python_harness_runtime.identity(source) == later
    assert session.run("exam", 0, "--side", "eval", "--from-run", "study",
                       "--from-round", "1", tasks="t05") == 0
    assert curve(session.root / "exam")[0]["harness_runtime_identity"] == original
    assert session.prepare.call_args.kwargs["binding"] == original
    assert session.measured[-1] == ("t05", original)


@pytest.mark.parametrize("damage", ["curve", "point", "field", "null", "malformed", "other-round"])
def test_eval_refuses_missing_or_invalid_checkpoint_identity_before_preparation(session, damage, capsys):
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    points = curve(directory)
    source = directory / "state_after_round1"
    later = python_harness_runtime.prepare(source, force=True)["identity"]
    assert later != points[1]["harness_runtime_identity"]
    assert python_harness_runtime.identity(source) == later
    path = directory / "curve.jsonl"
    if damage == "curve":
        path.unlink()
    else:
        if damage == "point":
            points.pop()
        elif damage == "field":
            points[1].pop("harness_runtime_identity")
        elif damage == "null":
            points[1]["harness_runtime_identity"] = None
        elif damage == "malformed":
            points[1]["harness_runtime_identity"] = {"kind": "broken"}
        else:
            points[1]["harness_runtime_identity"] = points[0]["harness_runtime_identity"]
        path.write_text("".join(json.dumps(point) + "\n" for point in points))
    prepares, measurements = session.prepare.call_count, session.measurement.call_count
    assert session.run("exam", 0, "--side", "eval", "--from-run", "study",
                       "--from-round", "1", tasks="t05") == 2
    assert "refusing --from-run:" in capsys.readouterr().err
    assert session.prepare.call_count == prepares
    assert session.measurement.call_count == measurements
    assert not (session.root / "exam").exists()


def test_eval_refuses_corrupt_original_artifact_without_selecting_new_publication(session, capsys):
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    original = curve(directory)[1]["harness_runtime_identity"]
    source = directory / "state_after_round1"
    later = python_harness_runtime.prepare(source, force=True)["identity"]
    assert python_harness_runtime.identity(source) == later and later != original
    path = Path(original["artifact"]["path"]) / "pyvenv.cfg"
    mode, content = stat.S_IMODE(path.stat().st_mode), path.read_bytes()
    prepares, measurements = session.prepare.call_count, session.measurement.call_count
    try:
        path.chmod(mode | stat.S_IWUSR)
        path.write_bytes(content + b"\n# changed artifact bytes\n")
        path.chmod(mode)
        assert session.run("exam", 0, "--side", "eval", "--from-run", "study",
                           "--from-round", "1", tasks="t05") == 2
        assert "refusing --from-run:" in capsys.readouterr().err
        assert session.prepare.call_count == prepares
        assert session.measurement.call_count == measurements
        assert path.read_bytes() != content
        assert not (session.root / "exam").exists()
    finally:
        path.chmod(mode | stat.S_IWUSR)
        path.write_bytes(content)
        path.chmod(mode)


def test_legacy_eval_keeps_optional_curve_provenance(tmp_path):
    from harnessgrad.loops.eval_side import _eval_from_record

    source = tmp_path / "legacy"
    shutil.copytree(ROOT / "tests/fixtures/base_harness/loop_plain", source)
    assert _eval_from_record(tmp_path, "older-run", 2, source=source) == {
        "run_id": "older-run", "round": 2}


def test_partial_eval_resume_preserves_progress_and_does_not_prepare_again(session):
    assert session.run("study") == 0
    extra = ("--side", "eval", "--from-run", "study")
    session.pause[0] = "t06"
    with pytest.raises(KeyboardInterrupt, match="authored partial evaluation"):
        session.run("exam", 0, *extra, tasks="t05,t06")
    directory = session.root / "exam"
    progress = directory / "progress/round-0/t05.json"
    before = progress.read_bytes()
    metadata = json.loads((directory / "run_meta.json").read_text())
    assert json.loads(before)["verification_context"] == verification_context.project(
        metadata["verification_context"], ["t05"])
    prepares = session.prepare.call_count
    session.pause[0] = None
    assert session.run("exam", 0, *extra, "--resume", tasks="t05,t06") == 0
    assert session.prepare.call_count == prepares
    assert progress.read_bytes() == before
    assert [tid for tid, _ in session.measured] == ["t01", "t05", "t06"]
    assert len(list((directory / "harness-preparation/round-0").glob("attempt-*.json"))) == 1


@pytest.mark.parametrize("damage", ["missing", "changed", "malformed"])
def test_eval_progress_identity_is_checked_before_old_metadata_is_rewritten(session, damage):
    assert session.run("study", 1) == 0
    train = curve(session.root / "study")
    extra = ("--side", "eval", "--from-run", "study", "--from-round", "1")
    assert session.run("exam", 0, *extra, tasks="t05,t06") == 0
    directory = session.root / "exam"
    progress = directory / "progress/round-0/t05.json"
    body = json.loads(progress.read_text())
    if damage == "missing":
        body.pop("harness_runtime_identity")
    elif damage == "changed":
        body["harness_runtime_identity"] = train[0]["harness_runtime_identity"]
    else:
        body["harness_runtime_identity"] = {"kind": "broken"}
    progress.write_text(json.dumps(body))
    metadata = directory / "run_meta.json"
    before = metadata.read_bytes(), progress.read_bytes()
    workspace = Path(json.loads(before[0])["workspace"])
    sentinel = workspace / "must-survive-refusal"
    sentinel.write_text("unchanged")
    assert session.run("exam", 0, *extra, "--resume", tasks="t05,t06") == 2
    assert before == (metadata.read_bytes(), progress.read_bytes())
    assert sentinel.read_text() == "unchanged"


@pytest.mark.parametrize("round_index", [0, 1])
def test_resume_never_invents_context_for_existing_measured_or_rejected_points(session, round_index, capsys):
    session.edits[0] = "drop-runtime"
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    points = curve(directory)
    assert points[0]["measured_by_platform"] and points[1]["unmeasured"]
    points[round_index].pop("verification_context")
    path = directory / "curve.jsonl"
    path.write_text("".join(json.dumps(point) + "\n" for point in points))
    metadata = directory / "run_meta.json"
    workspace = Path(json.loads(metadata.read_text())["workspace"])
    sentinel = workspace / "preserve-on-missing-context"
    sentinel.write_text("original")
    before = metadata.read_bytes(), path.read_bytes()
    prepares, measurements = session.prepare.call_count, session.measurement.call_count
    session.edits[0] = "repair-runtime"
    assert session.run("study", 2, "--resume") == 2
    assert "verification context is missing" in capsys.readouterr().err
    assert (metadata.read_bytes(), path.read_bytes()) == before
    assert sentinel.read_text() == "original"
    assert (session.prepare.call_count, session.measurement.call_count) == (prepares, measurements)


@pytest.mark.parametrize("refusal", ["unmeasured", "measured_by_platform"])
def test_planned_context_cannot_turn_a_rejected_point_into_reusable_scores(refusal):
    from harnessgrad.loops.mode_a import _matching_verification_context, _identical_prior
    context = verification_context.create({"t01": "a" * 64, "t02": "b" * 64})
    measured = {"identity": {"harness_sha": "same"},
                "sampling": {"trials": 1, "task_ids": ["t01", "t02"]},
                "verification_context": context, "measured_by_platform": True,
                "per_task": {"t01": 1},
                "invalid": {"t02": {"stage": "scorer"}}}
    rejected = copy.deepcopy(measured)
    rejected[refusal] = {"reason": "candidate was refused"} if refusal == "unmeasured" else False
    assert not _matching_verification_context(rejected, context)
    assert _identical_prior([rejected], "same", 1, verification_context=context) is None
    # A genuine partial measurement is still usable; task-level invalidity does
    # not turn its measured scores into a rejected candidate.
    assert _matching_verification_context(measured, context)
    assert _identical_prior([measured], "same", 1, verification_context=context) is measured


def test_measured_result_missing_context_is_not_repaired_from_the_plan(session, monkeypatch, capsys):
    from harnessgrad.loops import mode_a
    measure = mode_a.evaluate
    def omit_measurement_context(*args, **kwargs):
        result = measure(*args, **kwargs)
        result.pop("verification_context")
        return result
    monkeypatch.setattr(mode_a, "evaluate", omit_measurement_context)
    assert session.run("study", 1) == 0
    directory = session.root / "study"
    points = curve(directory)
    assert points[1]["measured_by_platform"] and points[1]["per_task"]
    assert "verification_context" not in points[1]
    metadata, curve_path = directory / "run_meta.json", directory / "curve.jsonl"
    before = metadata.read_bytes(), curve_path.read_bytes()
    measurements, preparations = session.measurement.call_count, session.prepare.call_count
    assert session.run("study", 2, "--resume") == 2
    assert "verification context is missing" in capsys.readouterr().err
    assert (metadata.read_bytes(), curve_path.read_bytes()) == before
    assert (session.measurement.call_count, session.prepare.call_count) == (measurements, preparations)
