"""Offline regression tests for the inherited six-task admission boundary.

Docker calls are fakes. No benchmark, provider, registry or released task payload is
used; temporary ledgers and fabricated reports stay under pytest's local basetemp.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.admission import pilot_admission as admission
POLICY = dict(growth_bytes_per_device=4, floor_bytes_per_device=8,
              inodes_per_device=100, floor_inodes_per_device=10,
              cpus=2, memory_bytes=9, pids=20)
HOST = dict(cpus=20, memory_bytes=100, pids=1000)


def result(args, code=0, out="", err=""):
    return subprocess.CompletedProcess(args, code, out, err)


@pytest.fixture(autouse=True)
def deny_real_execution(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("offline test attempted a real subprocess")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    import socket
    monkeypatch.setattr(socket, "socket", refuse)


@pytest.fixture
def manifest():
    # Synthetic metadata only; never open a benchmark manifest or payload.
    tasks = []
    for name, digest in zip(admission.PILOT, admission.IMAGE_IDS):
        prefix = "alexgshaw/" if name == "build-pmars" else "docker.1panel.live/alexgshaw/"
        tasks.append(dict(task_id=name, image=prefix + name + "@sha256:" + digest,
                          image_id="sha256:" + digest, scorer=dict(network="none",
                          dependency_closure_verified=False, trusted_runtime_verified=False,
                          state_export_contract_verified=False)))
    return dict(schema="harnessgrad.cached-pilot-admission.v1",
                inherited_pilot_sha256=admission.INHERITED_SHA256, task_ids=list(admission.PILOT),
                jobs=1, benchmark_enabled=False, allowed_execution="synthetic_local_runtime_controls",
                budgets=dict(pulls=0, builds=0, model_calls=0, benchmark_trials=0, heldout_reads=0),
                tasks=tasks, runtime=dict(network="none", pull="never", read_only_root=True,
                memory_bytes=256 << 20, cpus=1, pids=128, tmpfs_bytes=16 << 20, tmpfs_inodes=4096, timeout_s=30),
                reservation=dict(growth_bytes_per_device=4 << 30, floor_bytes_per_device=8 << 30,
                inodes_per_device=100000, floor_inodes_per_device=10000, cpus=2, memory_bytes=9 << 30, pids=2048))


def acquire(ledger, *, free=12, inodes=1000):
    return ledger.acquire("f" * 64, POLICY,
                          {"device": dict(free_bytes=free, free_inodes=inodes)}, HOST)


def test_device_aliases_are_reserved_once(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("docker", "containerd", "evidence")]
    for path in paths:
        path.mkdir()
    available = iter((30, 20, 25))
    monkeypatch.setattr(admission.os, "statvfs", lambda path: SimpleNamespace(
        f_bavail=next(available), f_frsize=1, f_favail=1000))
    devices = admission.filesystem_capacity(paths)
    assert len(devices) == 1
    row = next(iter(devices.values()))
    assert row["free_bytes"] == 20
    assert row["paths"] == [str(path) for path in paths]


@pytest.mark.parametrize("free,inodes", [(11, 1000), (1000, 109)])
def test_disk_or_inode_floor_refuses_before_allocation(tmp_path, free, inodes):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    with pytest.raises(admission.AdmissionError, match="disk/inode"):
        acquire(ledger, free=free, inodes=inodes)
    assert not ledger.path.exists()


def test_concurrent_admission_cannot_claim_same_headroom(tmp_path):
    path = tmp_path / "ledger.json"
    def attempt(_):
        try:
            return acquire(admission.ReservationLedger(path))
        except admission.AdmissionError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(attempt, range(2)))
    assert sum(lease is not None for lease in leases) == 1
    assert len(json.loads(path.read_text())["leases"]) == 1


@pytest.mark.parametrize("state", ["active", "quarantined"])
def test_old_or_quarantined_lease_retains_capacity(tmp_path, state):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    lease = acquire(ledger)
    ledger.update(lease, state=state, created_at=0)
    with pytest.raises(admission.AdmissionError, match="headroom"):
        acquire(admission.ReservationLedger(ledger.path))


def test_host_resources_are_also_reserved(tmp_path):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    host = {**HOST, "memory_bytes": 9}
    devices = {"device": dict(free_bytes=1000, free_inodes=1000)}
    ledger.acquire("f" * 64, POLICY, devices, host)
    with pytest.raises(admission.AdmissionError, match="memory_bytes"):
        ledger.acquire("f" * 64, POLICY, devices, host)


@pytest.mark.parametrize("text", ["{", '{"schema":"wrong","leases":{}}'])
def test_corrupt_ledger_is_not_free_capacity(tmp_path, text):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    ledger.path.write_text(text)
    with pytest.raises(admission.AdmissionError):
        acquire(ledger)
    assert ledger.path.read_text() == text


def test_unobserved_disk_capacity_must_not_be_admitted(tmp_path):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    with pytest.raises(admission.AdmissionError):
        ledger.acquire("f" * 64, POLICY, {}, HOST)
    assert not ledger.path.exists()


def test_negative_disk_reservation_must_not_be_admitted(tmp_path):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    policy = {**POLICY, "growth_bytes_per_device": -4}
    with pytest.raises(admission.AdmissionError):
        ledger.acquire("f" * 64, policy,
                       {"device": dict(free_bytes=12, free_inodes=1000)}, HOST)
    assert not ledger.path.exists()


@pytest.mark.parametrize("index", range(6))
def test_each_cached_task_uses_pull_never_and_no_network(manifest, index):
    task = manifest["tasks"][index]
    command = admission.control_command(task, "owned", "lease", "true")
    assert command[:2] == ["create", "--pull=never"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--memory") + 1] == "256m"
    assert command[command.index("--pids-limit") + 1] == "128"
    assert command[command.index("--entrypoint") + 1] == "/bin/sh"
    assert command[-3:] == [task["image"], "-c", "true"]


def test_mounts_cannot_override_no_network_policy(manifest):
    with pytest.raises(admission.AdmissionError):
        admission.control_command(manifest["tasks"][0], "owned", "lease", "true",
                                  mounts=("--network", "host"))


def test_docker_context_proxy_and_provider_environment_is_not_inherited(monkeypatch):
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "HTTP_PROXY", "HTTPS_PROXY",
                "OPENAI_API_KEY", "HG_AGENT_API_KEY", "HG_AGENT_BASE_URL"):
        monkeypatch.setenv(key, "synthetic-sentinel")
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return result(argv)
    monkeypatch.setattr(subprocess, "run", fake_run)
    admission.LocalDocker().call("image", "inspect", "sha256:" + "a" * 64)
    argv, options = calls[0]
    assert argv[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
    assert set(options["env"]) <= {"PATH", "LANG"}


@pytest.mark.parametrize("network", ["bridge", "host"])
def test_scorer_network_change_is_refused(tmp_path, manifest, network):
    manifest["tasks"][0]["scorer"]["network"] = network
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(admission.AdmissionError, match="egress"):
        admission.load_manifest(path)


def test_cached_images_do_not_attest_scorer_dependencies(manifest):
    blockers = admission.benchmark_blockers(manifest)
    for task in manifest["tasks"]:
        for field in ("dependency_closure_verified", "trusted_runtime_verified",
                      "state_export_contract_verified"):
            assert f"{task['task_id']}: {field}" in blockers
    assert len(blockers) == 20
    for task in manifest["tasks"]:
        for field in ("dependency_closure_verified", "trusted_runtime_verified",
                      "state_export_contract_verified"):
            task["scorer"][field] = True
    assert admission.benchmark_blockers(manifest) == blockers


class FakeDocker:
    def __init__(self, lease, task):
        self.lease, self.task = lease, task
        self.calls = []
        self.objects = {}
        self.remove_failure = False
        self.unknown_absence = False
        self.image_missing = False
        self.create_timeout = False

    def inspect_image(self, task):
        self.calls.append(("image", "inspect", task["image"]))
        if self.image_missing:
            raise admission.AdmissionError("cached image unavailable")
        return {}

    def inspect_container(self, ref):
        if self.unknown_absence:
            raise admission.AdmissionError("daemon query failed")
        return self.objects.get(ref)

    def call(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            cid = "a" * 64
            info = dict(Id=cid, Name="/" + name, Image=self.task["image_id"], Mounts=[],
                        Config=dict(Labels={"harnessgrad.admission": self.lease,
                                            "harnessgrad.task": self.task["task_id"]}),
                        State=dict(Status="created", Running=False, ExitCode=0),
                        HostConfig=dict(NetworkMode="none", ReadonlyRootfs=True,
                                        Memory=256 << 20, MemorySwap=256 << 20,
                                        PidsLimit=128, NanoCpus=10**9,
                                        Tmpfs={"/tmp": admission.TMPFS}, LogConfig=admission.LOG_CONFIG,
                                        Privileged=False, CapDrop=["ALL"], SecurityOpt=["no-new-privileges"]))
            if "--mount" in args:
                info["Mounts"] = admission._mount_plan(tuple(args[args.index("--mount"):args.index("--mount") + 2]))
            self.objects[name] = self.objects[cid] = info
            if self.create_timeout:
                raise subprocess.TimeoutExpired(args, 30)
            return result(args, out=cid + "\n")
        if args[0] == "start":
            self.objects[args[-1]]["State"]["Status"] = "exited"
            return result(args, out="synthetic-control\n")
        if args[0] == "rm":
            if self.remove_failure:
                return result(args, code=1, err="synthetic removal failure")
            self.objects = {key: value for key, value in self.objects.items()
                            if value["Id"] != args[-1]}
            return result(args)
        if args[0] == "ps":
            return result(args, out="\n".join(key for key, info in self.objects.items()
                if info.get("Config", {}).get("Labels", {}).get("harnessgrad.admission") == self.lease))
        raise AssertionError(args)


@pytest.fixture
def session(tmp_path, manifest):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    lease = acquire(ledger)
    docker = FakeDocker(lease, manifest["tasks"][0])
    return admission.ControlSession(ledger, lease, docker), docker, manifest["tasks"][0]


def test_evicted_image_refuses_before_create(session):
    control, docker, task = session
    docker.image_missing = True
    with pytest.raises(admission.AdmissionError, match="cached image"):
        control.run(task, "true")
    assert not control.ledger.get(control.lease)["owned"]
    assert all(call[0] != "create" for call in docker.calls)


def test_create_timeout_keeps_intent_and_can_reconcile_exact_owner(session):
    control, docker, task = session
    docker.create_timeout = True
    with pytest.raises(subprocess.TimeoutExpired):
        control.run(task, "true")
    owned = control.ledger.get(control.lease)["owned"]
    assert len(owned) == 1 and owned[0]["id"] is None
    assert control.cleanup()["state"] == "released"
    assert not docker.objects


@pytest.mark.parametrize("failure", ["remove", "daemon", "ownership", "namespace"])
def test_cleanup_uncertainty_quarantines_and_retains_capacity(session, failure):
    control, docker, task = session
    control.run(task, "true")
    if failure == "remove":
        docker.remove_failure = True
    elif failure == "daemon":
        docker.unknown_absence = True
    elif failure == "ownership":
        for info in docker.objects.values():
            info["Config"]["Labels"]["harnessgrad.admission"] = "someone-else"
    else:
        docker.objects["unrecorded"] = dict(Id="b" * 64,
            Config=dict(Labels={"harnessgrad.admission": control.lease}))
    row = control.cleanup()
    assert row["state"] == "quarantined" and row["cleanup_errors"]
    with pytest.raises(admission.AdmissionError, match="headroom"):
        acquire(control.ledger)
    with pytest.raises(admission.AdmissionError, match="quarantined"):
        control.run(task, "true")
    if failure == "ownership":
        assert not any(call[0] == "rm" for call in docker.calls)


def test_verified_cleanup_releases_reservation(session):
    control, docker, task = session
    control.run(task, "true")
    assert control.cleanup()["state"] == "released"
    assert not docker.objects
    assert acquire(control.ledger) != control.lease


def test_unexpected_network_envelope_is_refused_before_start(session):
    control, docker, task = session
    inspect = docker.inspect_container
    def changed_runtime(reference):
        info = inspect(reference)
        if info is not None:
            info["HostConfig"]["NetworkMode"] = "host"
        return info
    docker.inspect_container = changed_runtime
    with pytest.raises(admission.AdmissionError):
        control.run(task, "true")
    assert not any(call[0] == "start" for call in docker.calls)
    assert control.ledger.get(control.lease)["state"] == "quarantined"


@pytest.mark.parametrize("reference", ["sha256:" + "a" * 64, "repo@sha256:" + "b" * 64])
def test_cached_pin_is_reinspected_and_eviction_never_runs(monkeypatch, reference):
    calls = []
    def missing(*args, **kwargs):
        calls.append(args)
        return result(args, code=1, err="No such image")
    docker = admission.LocalDocker()
    monkeypatch.setattr(docker, "call", missing)
    for _ in range(2):
        with pytest.raises(admission.AdmissionError):
            docker.require_local_image(reference)
    assert len(calls) == 2
    assert all(call[:2] == ("image", "inspect") for call in calls)


def test_bare_pin_identity_mismatch_is_refused(monkeypatch):
    docker = admission.LocalDocker()
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs:
                        result(args, out=json.dumps([dict(Id="sha256:" + "b" * 64)])))
    with pytest.raises(admission.AdmissionError, match="identity mismatch"):
        docker.require_local_image("sha256:" + "a" * 64)


def test_unrelated_not_found_message_cannot_prove_owned_container_absent(monkeypatch):
    docker = admission.LocalDocker()
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs: result(
        args, code=1, err="Cannot connect to daemon; no such container: unrelated"))
    with pytest.raises(admission.AdmissionError, match="absence unproven"):
        docker.inspect_container("owned")


@pytest.mark.parametrize("raw,report,exit_code", [
    ("0", None, 0), ("1", None, 0), ("nan", {}, 0),
    ("1", {"results": {"summary": {}}}, 1),
])
def test_missing_dependency_or_scorer_report_is_unmeasured(raw, report, exit_code):
    verdict = admission.binary_ctrf_verdict(raw, report, exit_code)
    assert verdict["measured"] is False and verdict["score"] is None


@pytest.mark.parametrize("score", [0, 1])
def test_complete_synthetic_evidence_can_measure_pass_and_fail(score):
    report = dict(results=dict(summary=dict(tests=1, passed=score, failed=1-score,
                                           skipped=0, pending=0, other=0)))
    verdict = admission.binary_ctrf_verdict(str(score), report, 0)
    assert verdict["measured"] and verdict["score"] == score


def test_actor_executable_or_symlink_cannot_enter_trusted_artifacts(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "answer.txt").write_text("synthetic-data")
    (root / "sh").write_text("forged-toolchain")
    with pytest.raises(admission.AdmissionError, match="only a sealed"):
        admission.trusted_artifact_mount(root, "unused")
    (root / "sh").unlink()
    (root / "answer.txt").unlink()
    (root / "answer.txt").symlink_to(tmp_path / "outside-artifact")
    with pytest.raises(admission.AdmissionError, match="only a sealed"):
        admission.trusted_artifact_mount(root, "unused")


@pytest.mark.parametrize("key", list(POLICY))
@pytest.mark.parametrize("bad", [-1, True, 1.5, float("nan"), "4", None])
def test_invalid_reservation_never_changes_existing_ledger(tmp_path, key, bad):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    acquire(ledger)
    before = ledger.path.read_bytes()
    with pytest.raises(admission.AdmissionError):
        ledger.acquire("f" * 64, {**POLICY, key: bad},
                       {"device": dict(free_bytes=1000, free_inodes=1000)}, HOST)
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("section,key", [("device", "free_bytes"), ("device", "free_inodes"),
                                        *[("host", key) for key in HOST]])
@pytest.mark.parametrize("bad", [-1, True, 1.5, float("inf"), "4", None])
def test_invalid_observations_do_not_persist(tmp_path, section, key, bad):
    devices = {"device": dict(free_bytes=1000, free_inodes=1000)}
    host = HOST.copy()
    (devices["device"] if section == "device" else host)[key] = bad
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    with pytest.raises(admission.AdmissionError):
        ledger.acquire("f" * 64, POLICY, devices, host)
    assert not ledger.path.exists()


@pytest.mark.parametrize("mounts", [("--network=host",), ("--privileged", "true"),
    ("--mount", "type=bind,src=/tmp,dst=/control-input"),
    ("--mount", "type=bind,src=/tmp,dst=/control-input,readonly,bind-propagation=shared"),
    ("--mount", "type=bind,src=/tmp/../etc,dst=/control-input,readonly"),
    ("--mount", "type=bind,src=/tmp,dst=/var/run/docker.sock,readonly"),
    ("--mount", "type=bind,src=/tmp,dst=/control-input,readonly", "--network", "host")])
def test_mount_flags_are_rejected_before_intent_or_create(session, mounts):
    control, docker, task = session
    with pytest.raises(admission.AdmissionError):
        control.run(task, "true", mounts=mounts)
    assert not control.ledger.get(control.lease)["owned"]
    assert not any(call[0] in ("create", "start") for call in docker.calls)


@pytest.mark.parametrize("section,key,bad", [
    ("HostConfig", "NetworkMode", "host"),
    ("HostConfig", "ReadonlyRootfs", False),
    ("HostConfig", "Memory", 0), ("HostConfig", "MemorySwap", -1),
    ("HostConfig", "PidsLimit", 0), ("HostConfig", "NanoCpus", 0),
    ("HostConfig", "Privileged", True), ("HostConfig", "CapDrop", []),
    ("HostConfig", "CapAdd", ["SYS_ADMIN"]), ("HostConfig", "SecurityOpt", []),
    ("HostConfig", "Tmpfs", {}), ("HostConfig", "LogConfig", {}),
    ("HostConfig", "PidMode", "host"), ("HostConfig", "IpcMode", "host"),
    ("HostConfig", "Binds", ["/var/run/docker.sock:/var/run/docker.sock"]),
    ("HostConfig", "Devices", [dict(PathOnHost="/dev/sda")]),
    ("Config", "Volumes", {"/unmanaged": {}}),
    ("State", "Running", True), ("State", "Status", "running"),
    ("NetworkSettings", "Networks", {"bridge": {}}),
])
def test_each_inspected_envelope_mismatch_quarantines_before_start(session, section, key, bad):
    control, docker, task = session
    inspect = docker.inspect_container
    def changed(reference):
        info = inspect(reference)
        if info is not None:
            info.setdefault(section, {})[key] = bad
        return info
    docker.inspect_container = changed
    with pytest.raises(admission.AdmissionError, match="envelope"):
        control.run(task, "true")
    assert not any(call[0] == "start" for call in docker.calls)
    row = control.ledger.get(control.lease)
    assert row["state"] == "quarantined" and row["owned"][0]["id"]
    with pytest.raises(admission.AdmissionError):
        acquire(control.ledger)
    assert control.cleanup()["state"] == "released"


@pytest.mark.parametrize("change", ["unexpected", "writable", "source", "missing"])
def test_actual_mounts_must_match_requested_artifact_bind(session, tmp_path, change):
    control, docker, task = session
    mounts = ("--mount", f"type=bind,src={tmp_path},dst=/control-input,readonly")
    inspect = docker.inspect_container
    def changed(reference):
        info = inspect(reference)
        if info is not None:
            if change == "unexpected":
                info["Mounts"].append(dict(Type="volume", Source="unmanaged", Destination="/x", RW=True))
            elif change == "missing":
                info["Mounts"] = []
            else:
                info["Mounts"][0]["RW" if change == "writable" else "Source"] = True if change == "writable" else "/unrelated"
        return info
    docker.inspect_container = changed
    with pytest.raises(admission.AdmissionError, match="envelope"):
        control.run(task, "true", mounts=mounts)
    assert not any(call[0] == "start" for call in docker.calls)
    assert control.ledger.get(control.lease)["state"] == "quarantined"


def test_sealed_artifact_mount_is_admitted(session, tmp_path):
    import hashlib
    control, docker, task = session
    root = tmp_path / "sealed"
    root.mkdir()
    raw = b"synthetic data"
    (root / "answer.txt").write_bytes(raw)
    mounts = admission.trusted_artifact_mount(root, hashlib.sha256(raw).hexdigest())
    assert control.run(task, "true", mounts=mounts)["exit_code"] == 0
    assert control.cleanup()["state"] == "released"


@pytest.mark.parametrize("image_id", ["sha256:short", "not-an-id", "repo@sha256:" + "a" * 64, None])
def test_invalid_inspected_image_id_is_refused(monkeypatch, image_id):
    docker = admission.LocalDocker()
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs: result(args, out=json.dumps([dict(Id=image_id)])))
    with pytest.raises(admission.AdmissionError, match="image ID"):
        docker.require_local_image("sha256:" + "a" * 64)


@pytest.mark.parametrize("error", [OSError("synthetic daemon failure"), subprocess.TimeoutExpired("inspect", 30)])
@pytest.mark.parametrize("kind", ["image", "container"])
def test_inspect_failures_are_admission_refusals(monkeypatch, kind, error):
    docker = admission.LocalDocker()
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr(docker, "call", broken)
    with pytest.raises(admission.AdmissionError, match="inspection failed"):
        if kind == "image":
            docker.require_local_image("sha256:" + "a" * 64)
        else:
            docker.inspect_container("owned")


@pytest.mark.parametrize("prefix", ["Error response from daemon: No such container: ",
    "Error: No such container: ", "Error: No such object: "])
def test_only_exact_complete_absence_reply_is_accepted(monkeypatch, prefix):
    docker = admission.LocalDocker()
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs: result(args, code=1, out="[]", err=prefix + "owned\n"))
    assert docker.inspect_container("owned") is None
    with pytest.raises(admission.AdmissionError):
        docker.inspect_container("unrelated")


@pytest.mark.parametrize("err", ["Error: No such object: owned-other",
    "Cannot connect to daemon; no such container: owned",
    "Error: No such object: owned\nCannot connect to daemon", "no such container: owned"])
def test_noncanonical_absence_never_frees_capacity(session, err):
    control, docker, task = session
    control.run(task, "true")
    original = docker.call
    strict = admission.LocalDocker()
    strict.call = lambda *args, **kwargs: result(args, code=1, err=err)
    def removal_then_bad_inspect(*args, **kwargs):
        reply = original(*args, **kwargs)
        if args[0] == "rm":
            docker.inspect_container = strict.inspect_container
        return reply
    docker.call = removal_then_bad_inspect
    assert control.cleanup()["state"] == "quarantined"
    with pytest.raises(admission.AdmissionError):
        acquire(control.ledger)


@pytest.mark.parametrize("changed", ["id", "name", "task"])
def test_cleanup_refuses_changed_owned_identity(session, changed):
    control, docker, task = session
    control.run(task, "true")
    info = next(iter(docker.objects.values()))
    if changed == "task":
        info["Config"]["Labels"]["harnessgrad.task"] = "unrelated"
    else:
        info["Id" if changed == "id" else "Name"] = "b" * 64 if changed == "id" else "/unrelated"
    assert control.cleanup()["state"] == "quarantined"
    assert not any(call[0] == "rm" for call in docker.calls)


def test_cleanup_never_removes_unrelated_container_or_unknown_owned_object(session):
    control, docker, task = session
    control.run(task, "true")
    docker.objects["unrelated"] = dict(Id="b" * 64, Config=dict(Labels={"harnessgrad.admission": "other"}))
    docker.objects["unknown-owned"] = dict(Id="c" * 64, Config=dict(Labels={"harnessgrad.admission": control.lease}))
    assert control.cleanup()["state"] == "quarantined"
    assert set(docker.objects) == {"unrelated", "unknown-owned"}
    assert [call[-1] for call in docker.calls if call[0] == "rm"] == ["a" * 64]


def test_atomic_quarantine_write_failure_retains_previous_allocation(tmp_path, monkeypatch):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    lease = acquire(ledger)
    before = ledger.path.read_bytes()
    def fail_replace(*args):
        raise OSError("synthetic crash before atomic replacement")
    with monkeypatch.context() as patch:
        patch.setattr(admission.os, "replace", fail_replace)
        with pytest.raises(OSError):
            ledger.update(lease, state="quarantined")
    assert ledger.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(admission.AdmissionError):
        acquire(ledger)
    ledger.update(lease, state="quarantined")
    assert admission.ReservationLedger(ledger.path).get(lease)["state"] == "quarantined"


def test_failed_allocation_write_creates_no_partial_lease(tmp_path, monkeypatch):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    def fail_fsync(*args):
        raise OSError("synthetic persistence failure")
    monkeypatch.setattr(admission.os, "fsync", fail_fsync)
    with pytest.raises(OSError):
        acquire(ledger)
    assert not ledger.path.exists() and not list(tmp_path.glob("*.tmp"))


def test_invalid_update_is_atomic_and_quarantine_cannot_be_reactivated(tmp_path):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    lease = acquire(ledger)
    before = ledger.path.read_bytes()
    with pytest.raises(admission.AdmissionError):
        ledger.update(lease, resources={**POLICY, "cpus": -1})
    assert ledger.path.read_bytes() == before
    ledger.update(lease, state="quarantined")
    with pytest.raises(admission.AdmissionError):
        ledger.update(lease, state="active")


@pytest.mark.parametrize("stage", ["ownership", "start"])
def test_intent_persistence_failure_prevents_next_daemon_mutation(session, monkeypatch, stage):
    control, docker, task = session
    update = control.ledger.update
    def fail_intent(lease, **fields):
        owned = fields.get("owned", [])
        if owned and ((stage == "ownership" and owned[-1]["id"] is None)
                      or (stage == "start" and owned[-1].get("start_attempted"))):
            raise OSError("synthetic intent persistence failure")
        return update(lease, **fields)
    monkeypatch.setattr(control.ledger, "update", fail_intent)
    with pytest.raises(OSError):
        control.run(task, "true")
    assert not any(call[0] == ("create" if stage == "ownership" else "start") for call in docker.calls)
    row = control.ledger.get(control.lease)
    assert row["state"] == ("active" if stage == "ownership" else "quarantined")
    with pytest.raises(admission.AdmissionError):
        acquire(control.ledger)


def test_failed_release_persistence_cannot_free_capacity(session, monkeypatch):
    control, docker, task = session
    control.run(task, "true")
    def fail_replace(*args):
        raise OSError("synthetic release persistence failure")
    with monkeypatch.context() as patch:
        patch.setattr(admission.os, "replace", fail_replace)
        with pytest.raises(OSError):
            control.cleanup()
    assert not docker.objects
    assert control.ledger.get(control.lease)["state"] == "active"
    with pytest.raises(admission.AdmissionError):
        acquire(control.ledger)
    assert control.cleanup()["state"] == "released"
    assert control.cleanup()["state"] == "released"


def test_cleanup_releases_only_after_canonical_exact_id_absence(session):
    control, docker, task = session
    control.run(task, "true")
    call = docker.call
    queries = []
    strict = admission.LocalDocker()
    def query(*args, **kwargs):
        queries.append(args)
        assert args == ("container", "inspect", "a" * 64)
        return result(args, code=1, out="[]", err="Error: No such object: " + args[-1])
    strict.call = query
    def remove(*args, **kwargs):
        reply = call(*args, **kwargs)
        if args[0] == "rm":
            docker.inspect_container = strict.inspect_container
        return reply
    docker.call = remove
    assert control.cleanup()["state"] == "released"
    assert len(queries) == 1


@pytest.mark.parametrize("case", ["matching-bare", "matching-repo", "wrong-repo", "wrong-expected"])
def test_image_identity_binds_both_id_and_repository_digest(monkeypatch, case):
    docker = admission.LocalDocker()
    image_id = "sha256:" + "a" * 64
    image = image_id if case == "matching-bare" else "repo@sha256:" + "b" * 64
    digests = [image] if case != "wrong-repo" else ["unrelated@sha256:" + "b" * 64]
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs: result(args,
        out=json.dumps([dict(Id=image_id, RepoDigests=digests)])))
    expected = "sha256:" + "c" * 64 if case == "wrong-expected" else None
    if case.startswith("wrong"):
        with pytest.raises(admission.AdmissionError, match="identity mismatch"):
            docker.require_local_image(image, expected)
    else:
        assert docker.require_local_image(image, expected) == image_id


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(devices={}),
    lambda row: row["resources"].update(cpus=-1),
    lambda row: row["devices"]["device"].update(bytes=-1),
    lambda row: row.update(observed_devices={}),
    lambda row: row.update(owned=[dict(name="foreign", id="short", task_id=admission.PILOT[0])]),
])
def test_malformed_persisted_lease_is_not_free_capacity(tmp_path, mutate):
    ledger = admission.ReservationLedger(tmp_path / "ledger.json")
    lease = acquire(ledger)
    data = json.loads(ledger.path.read_text())
    mutate(data["leases"][lease])
    ledger.path.write_text(json.dumps(data))
    before = ledger.path.read_bytes()
    with pytest.raises(admission.AdmissionError):
        acquire(ledger)
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("field", ["Id", "Name", "Image", "owner", "task"])
def test_created_identity_mismatch_never_starts(session, field):
    control, docker, task = session
    inspect = docker.inspect_container
    def changed(reference):
        info = inspect(reference)
        if info is not None:
            if field in ("owner", "task"):
                info["Config"]["Labels"]["harnessgrad." + ("admission" if field == "owner" else "task")] = "unrelated"
            else:
                info[field] = "unrelated"
        return info
    docker.inspect_container = changed
    with pytest.raises(admission.AdmissionError, match="envelope"):
        control.run(task, "true")
    assert not any(call[0] == "start" for call in docker.calls)
    assert control.ledger.get(control.lease)["state"] == "quarantined"


def test_start_timeout_retains_attempt_and_requires_cleanup(session):
    control, docker, task = session
    call = docker.call
    def timeout(*args, **kwargs):
        if args[0] == "start":
            assert control.ledger.get(control.lease)["owned"][0]["start_attempted"] is True
            raise subprocess.TimeoutExpired(args, 30)
        return call(*args, **kwargs)
    docker.call = timeout
    with pytest.raises(subprocess.TimeoutExpired):
        control.run(task, "true")
    assert control.ledger.get(control.lease)["state"] == "quarantined"
    with pytest.raises(admission.AdmissionError):
        acquire(control.ledger)
    assert control.cleanup()["state"] == "released"


@pytest.mark.parametrize("out,code", [("unexpected stdout", 1), ("[]", 2), ("[]", 0)])
def test_absence_reply_requires_failure_code_and_empty_object_result(monkeypatch, out, code):
    docker = admission.LocalDocker()
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs: result(
        args, code=code, out=out, err="Error: No such object: owned"))
    with pytest.raises(admission.AdmissionError):
        docker.inspect_container("owned")


@pytest.mark.parametrize("raw", ["{", "[]", "null", '[{},{}]', '{"Id":"wrong-shape"}'])
@pytest.mark.parametrize("kind", ["image", "container"])
def test_malformed_inspection_is_not_identity_or_absence_proof(monkeypatch, raw, kind):
    docker = admission.LocalDocker()
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs: result(args, out=raw))
    with pytest.raises(admission.AdmissionError):
        if kind == "image":
            docker.require_local_image("sha256:" + "a" * 64)
        else:
            docker.inspect_container("owned")
