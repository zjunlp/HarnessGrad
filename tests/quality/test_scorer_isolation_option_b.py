"""Option B production seams; synthetic Docker observations, no daemon or dataset.

The Docker double executes the sealed scorer locally against the real projection.
It is an integration control, not evidence of kernel isolation or H0 readiness.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval.admission import artifact_admission as artifacts
from eval.admission import scorer_bundle as bundles
from eval.admission.pilot_admission import AdmissionError, LOG_CONFIG
from eval.run import runner
from eval.verification import verifier
from eval.verification.isolation import IsolationMismatch, require_comparable
from harnessgrad.records import _curve_point, _save_verdicts, _write_curve, test_rate_fields as point_fields


def point(result):
    return _curve_point(run_id="option-b", round_index=0, identity={"harness_sha": "fixture"},
                        sampling={}, scores=list(result["per_task"].values()),
                        task_ids=list(result["per_task"]), cost={}, cumulative={},
                        **point_fields(result))


@pytest.fixture
def scorer(tmp_path):
    root = tmp_path / "closure"
    root.mkdir()
    shutil.copyfile(sys.executable, root / "python")
    (root / "python").chmod(0o755)
    (root / "check.py").write_text(
        "import pathlib,sys\n"
        "passed = pathlib.Path(sys.argv[1]).read_text() == 'correct'\n"
        "print('completed_check=' + ('pass' if passed else 'fail'))\n"
        "sys.exit(0 if passed else 1)\n")
    rows = []
    for name, role, requires in [("python", "runtime", []), ("check.py", "scorer", ["python"])]:
        raw = (root / name).read_bytes()
        rows.append(dict(path=name, role=role, requires=requires,
                         bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    manifest = dict(schema=bundles.ISOLATION_SCHEMA, isolation_class="pristine-offline",
                    source_sha256="a" * 64, runtime_image_id="sha256:" + "b" * 64,
                    network="none", scorer=dict(entrypoint="check.py", files=rows,
                    argv=["/control-scorer/python", "/control-scorer/check.py", "/control-input/answer.txt"]))
    raw, pin = bundles.seal_bundle(manifest)
    (tmp_path / "bundle.json").write_bytes(raw)
    contract = dict(schema="harnessgrad.artifact-scorer.v1", task_id="t",
                    **{key: manifest[key] for key in ("source_sha256", "runtime_image_id", "scorer")},
                    export=dict(files=[dict(path="answer.txt", max_bytes=16)], max_bytes=16, max_inodes=2),
                    quota={"/tmp": dict(bytes=65536, inodes=32)})
    contract_raw = json.dumps(contract).encode()
    (tmp_path / "contract.json").write_bytes(contract_raw)
    declaration = dict(isolation_class="pristine-offline", manifest=str(tmp_path / "bundle.json"),
                       manifest_sha256=pin, root=str(root), artifact_contract=str(tmp_path / "contract.json"),
                       artifact_contract_sha256=hashlib.sha256(contract_raw).hexdigest())
    return dict(kind="command", argv=manifest["scorer"]["argv"], scorer_bundle=declaration)


@pytest.fixture
def docker_double(monkeypatch):
    state = SimpleNamespace(calls=[], info=None, mutate=lambda info: None,
                            started=False, exit_code=None, cleanup_failure=False)

    def docker(*args, timeout=30):
        state.calls.append(args)
        if args[0] == "create":
            cmd = list(args)
            mounts = []
            tmpfs, env = {}, []
            for i, arg in enumerate(cmd):
                if arg == "--mount":
                    fields = dict(part.split("=", 1) for part in cmd[i + 1].split(",") if "=" in part)
                    assert cmd[i + 1].endswith(",readonly")
                    mounts.append(dict(Type="bind", Source=fields["src"], Destination=fields["dst"], RW=False))
                if arg == "--tmpfs":
                    path, options = cmd[i + 1].split(":", 1)
                    tmpfs[path] = options
                if arg == "--env":
                    env.append(cmd[i + 1])
            image_index = next(i for i, arg in enumerate(cmd) if arg.startswith("sha256:"))
            state.info = dict(Image=cmd[image_index], State=dict(Status="created", Running=False),
                Config=dict(Volumes={}, User=cmd[cmd.index("--user") + 1] if "--user" in cmd else "",
                            Entrypoint=[cmd[cmd.index("--entrypoint") + 1]],
                            Cmd=cmd[image_index + 1:], Env=env, WorkingDir=cmd[cmd.index("--workdir") + 1]),
                NetworkSettings=dict(Networks={"none": {}}),
                HostConfig=dict(NetworkMode=cmd[cmd.index("--network") + 1],
                    ReadonlyRootfs="--read-only" in cmd, Privileged=False,
                    CapDrop=[cmd[cmd.index("--cap-drop") + 1]],
                    SecurityOpt=[cmd[cmd.index("--security-opt") + 1]],
                    LogConfig=copy.deepcopy(LOG_CONFIG), Tmpfs=tmpfs), Mounts=mounts)
            state.mutate(state.info)
        elif args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, json.dumps([state.info]), "")
        elif args[0] == "start":
            state.started = True
            mounts = state.info["Mounts"]
            projection = Path(next(m["Source"] for m in mounts if m["Destination"] == "/control-input"))
            assert sorted(p.name for p in projection.iterdir()) == ["answer.txt"]
            command = state.info["Config"]["Entrypoint"] + state.info["Config"]["Cmd"]
            for mount in mounts:
                command = [arg.replace(mount["Destination"], mount["Source"]) for arg in command]
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            state.exit_code = result.returncode
            state.info["State"] = dict(Status="exited", Running=False, ExitCode=result.returncode, OOMKilled=False)
            return subprocess.CompletedProcess(args, result.returncode, result.stdout, result.stderr)
        elif args[0] == "rm" and state.cleanup_failure:
            return subprocess.CompletedProcess(args, 1, "", "synthetic cleanup failure")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(verifier.container_mod, "docker", docker)
    return state


def actor(tmp_path, answer="correct"):
    work = tmp_path / "actor"
    work.mkdir()
    (work / "answer.txt").write_text(answer)
    (work / "unselected.py").write_text("raise AssertionError('not scorer input')")
    return work


@pytest.mark.parametrize("answer,expected", [("correct", 1.0), ("wrong", 0.0)])
def test_truthful_pristine_scorer_runs_and_class_reaches_point_and_saved_verdict(
        tmp_path, scorer, docker_double, monkeypatch, answer, expected):
    work = actor(tmp_path, answer)
    verdict = verifier._verify(work, scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert docker_double.started and verdict["score"] == expected, verdict
    assert verdict["scorer_isolation_class"] == "pristine-offline"
    assert verdict["scorer_isolation_evidence"]["cleanup"]["removed"] is True
    result = dict(verdict=verdict, trace="fixture", exit_code=0, stderr="")
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: result)
    measured = runner._evaluate(tmp_path, [{"task_id": "t"}], {}, sandbox=False)
    assert point(measured)["scorer_isolation_class"] == "pristine-offline"
    progress_dir = tmp_path / "records"
    _save_verdicts(progress_dir, 0, measured, ["t"])
    saved = json.loads((progress_dir / "verdicts/round-0/t.json").read_text())
    assert saved["scorer_isolation_class"] == "pristine-offline"
    assert saved["score"] == expected


def test_pristine_declaration_dispatches_from_run_one_to_curve(tmp_path, scorer, docker_double):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps(dict(name="fixture", version="1", entrypoint="agent.py")))
    (repo / "agent.py").write_text(
        "import argparse,pathlib\n"
        "p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir')\n"
        "a=p.parse_args();pathlib.Path(a.workdir,'answer.txt').write_text('correct')\n")
    result = runner.evaluate(repo, [dict(task_id="t", goal="synthetic")], {}, sandbox=False,
                             verifiers={"t": scorer}, run_id="binding-control")
    recorded = point(result)
    assert docker_double.started and recorded["score"] == 1.0
    assert recorded["scorer_isolation_class"] == "pristine-offline"
    assert recorded["per_task_scorer_isolation"] == {"t": "pristine-offline"}


def test_changed_bundle_declaration_cannot_reuse_cached_score(tmp_path, scorer, docker_double, monkeypatch):
    bound = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5,
                             task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert bound["score"] == 1.0
    calls = []
    def run(*_a, verifier, **_kw):
        calls.append(verifier["scorer_bundle"]["isolation_class"])
        return dict(verdict=bound if calls[-1] == "pristine-offline" else
                    dict(score=1.0, passed=True, scorer_isolation_class=calls[-1]),
                    trace="x", exit_code=0, stderr="")
    monkeypatch.setattr(runner, "run_one", run)
    cache = {}
    for class_ in ("pristine-offline", "snapshot-service"):
        result = runner._evaluate(tmp_path, [dict(task_id="t")], {}, cache=cache, run_id="binding-control",
            verifiers={"t": dict(scorer_bundle=dict(isolation_class=class_))})
        assert result["scorer_isolation_class"] == class_
    assert calls == ["pristine-offline", "snapshot-service"]


@pytest.mark.parametrize("change", ["network", "extra_mount", "snapshot"])
def test_false_pristine_runtime_is_refused_before_scorer_execution(tmp_path, scorer, docker_double, change):
    def mutate(info):
        if change == "network":
            info["HostConfig"]["NetworkMode"] = "bridge"
        elif change == "extra_mount":
            info["Mounts"].append(dict(Type="bind", Source=str(tmp_path), Destination="/actor", RW=False))
        else:
            info["Image"] = "sha256:" + "c" * 64
    docker_double.mutate = mutate
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] is None and verdict["unmeasured"]
    assert verdict["scorer_isolation_class"] == "unknown"
    assert not docker_double.started
    assert docker_double.calls[-1][0] == "rm"


def test_cleanup_failure_is_unmeasured_not_zero(tmp_path, scorer, docker_double):
    docker_double.cleanup_failure = True
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] is None
    assert verdict["scorer_isolation_class"] == "pristine-offline"
    assert "cleanup failed" in verdict["unmeasured"]


def test_different_class_executes_and_is_recorded_without_changing_service_mounts(tmp_path, monkeypatch):
    work = actor(tmp_path)
    seen = {}
    def run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(verifier.container_mod, "run", run)
    mounts = ("--mount", "type=bind,src=/platform/recording,dst=/recordings/api,readonly")
    monkeypatch.setattr(verifier.services_mod, "mounts_for", lambda *_: mounts)
    spec = dict(kind="command", argv=["check-service"],
                scorer_bundle=dict(isolation_class="snapshot-service"))
    v = verifier._verify(work, spec, "", sandbox=True, timeout_s=5,
                         env=dict(kind="exec", image="sha256:" + "b" * 64),
                         service_specs=[{"name": "api"}],
                         running=SimpleNamespace(network="task-service-network", recordings={}))
    assert v["passed"] and v["scorer_isolation_class"] == "snapshot-service"
    assert seen["network"] == "task-service-network"
    assert seen["extra_mounts"] == mounts
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: dict(verdict=v, trace="x", exit_code=0, stderr=""))
    measured = runner._evaluate(tmp_path, [{"task_id": "t"}], {}, sandbox=False)
    assert point(measured)["scorer_isolation_class"] == "snapshot-service"


def test_snapshot_verifier_keeps_dependency_network_and_records_actual_class(tmp_path, monkeypatch):
    snapshot_path = Path(__file__).resolve().parents[2] / "tests/fixtures/hg_wsnap/raw/015.stdout.txt"
    if not snapshot_path.is_file():
        pytest.skip("tests/fixtures/hg_wsnap is internal (see .gitignore) and absent from this clone")
    snapshot = snapshot_path.read_text().strip()
    monkeypatch.setattr(verifier.container_mod, "docker",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, snapshot, ""))
    seen = {}
    monkeypatch.setattr(verifier.container_mod, "create", lambda _name, **kw: seen.update(kw))
    monkeypatch.setattr(verifier.container_mod, "start", lambda *_: None)
    monkeypatch.setattr(verifier.container_mod, "remove_container", lambda *_: None)
    monkeypatch.setattr(verifier.container_mod, "exec_in", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "", ""))
    spec = dict(kind="command", argv=["bash", "/tests/test.sh"],
                scorer_bundle=dict(isolation_class="snapshot-service"))
    verdict = verifier._verify_container_state(snapshot, spec, expected_snapshot=snapshot, task_mount="/app", staging=tmp_path,
        timeout_s=5, run_id="fixture", task_id="t", network="bridge")
    assert verdict["passed"] and verdict["scorer_isolation_class"] == "snapshot-service"
    assert seen["image"] == snapshot and seen["network"] == "bridge"


def test_pristine_declaration_cannot_relabel_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.container_mod, "create", lambda *_a, **_kw: pytest.fail("snapshot started"))
    verdict = verifier._verify_container_state("actor-snapshot",
        dict(kind="command", argv=["true"], scorer_bundle=dict(isolation_class="pristine-offline")),
        task_mount="/app", staging=tmp_path, timeout_s=5, run_id="fixture", task_id="t")
    assert verdict["score"] is None and verdict["scorer_isolation_class"] == "unknown"


@pytest.mark.parametrize("change", ["command", "task", "bundle_bytes"])
def test_pristine_binding_mismatch_is_refused_before_runtime_creation(tmp_path, scorer, docker_double, change):
    if change == "command":
        scorer["argv"] = ["another-command"]
    elif change == "bundle_bytes":
        Path(scorer["scorer_bundle"]["manifest"]).write_text("{}")
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5,
                               task_id="other-task" if change == "task" else "t",
                               run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] is None and verdict["unmeasured"]
    assert not docker_double.calls


def test_comparison_across_two_classes_is_refused_with_reason():
    with pytest.raises(IsolationMismatch) as exc:
        require_comparable(dict(scorer_isolation_class="pristine-offline", score=1),
                           dict(scorer_isolation_class="snapshot-service", score=1))
    assert str(exc.value) == "scorer isolation class mismatch: pristine-offline, snapshot-service"
    print(f"REFUSED: {exc.value}")


@pytest.mark.parametrize("location", ["tasks", "trials", "cached"])
def test_mixed_classes_cannot_be_averaged(tmp_path, monkeypatch, location):
    def result(class_):
        return dict(verdict=dict(score=1.0, passed=True, scorer_isolation_class=class_),
                    trace="x", exit_code=0, stderr="")
    values = iter([result("pristine-offline"), result("snapshot-service")])
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_kw: next(values))
    tasks = [{"task_id": "a"}, {"task_id": "b"}] if location != "trials" else [{"task_id": "a"}]
    cache = {}
    if location == "cached":
        cache[":a:1"] = dict(score=1, trace="cached", verdict=result("snapshot-service")["verdict"])
    with pytest.raises(IsolationMismatch, match="pristine-offline, snapshot-service"):
        runner._evaluate(tmp_path, tasks, {}, cache=cache, trials=2 if location == "trials" else 1)


def test_curve_writer_refuses_mixed_classes_even_for_external_results():
    with pytest.raises(IsolationMismatch, match="class mismatch"):
        point(dict(per_task=dict(a=1, b=1), per_task_scorer_isolation={
            "a": "pristine-offline", "b": "snapshot-service"}))


def test_missing_classes_stay_unknown_and_unknown_comparison_is_invalid():
    # Curve-construction refusal asserted the write-path bug. Preserve the raw
    # score as explicitly noncomparable; absence still cannot establish topology.
    recorded = point(dict(per_task=dict(a=1)))
    assert recorded["score"] == 1 and recorded["scorer_isolation_class"] == "unknown"
    assert recorded["scorer_isolation_comparable"] is False
    with pytest.raises(IsolationMismatch, match="unknown"):
        require_comparable(recorded, recorded)
    with pytest.raises(IsolationMismatch, match="unknown"):
        require_comparable({}, {})


def test_curve_history_refuses_different_classes_before_writing(tmp_path):
    destination = tmp_path / "curve.jsonl"
    destination.write_text("preserved record\n")
    with pytest.raises(IsolationMismatch, match="class mismatch"):
        _write_curve(destination, [dict(scorer_isolation_class="pristine-offline"),
                                   dict(scorer_isolation_class="snapshot-service")])
    assert destination.read_text() == "preserved record\n"


def test_v2_bundle_class_is_pinned_and_does_not_relax_v1(scorer):
    b = scorer["scorer_bundle"]
    manifest = bundles.load_bundle(Path(b["manifest"]).read_bytes(), b["manifest_sha256"])
    manifest.update(isolation_class="snapshot-service", network="task-service-network")
    raw, pin = bundles.seal_bundle(manifest)
    assert pin != b["manifest_sha256"]
    assert bundles.verify_bundle(Path(b["root"]), raw, pin)["isolation_class"] == "snapshot-service"
    manifest["isolation_class"] = "pristine-offline"
    with pytest.raises(AdmissionError):
        bundles.seal_bundle(manifest)
