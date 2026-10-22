"""Synthetic, source-only scorer/export/quota regressions; no benchmark payloads."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.admission import artifact_admission as artifacts
from eval.admission.pilot_admission import AdmissionError, LOG_CONFIG


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("synthetic control attempted subprocess/network execution")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)


def seal(contract):
    raw = json.dumps(contract, sort_keys=True).encode()
    return raw, hashlib.sha256(raw).hexdigest()


@pytest.fixture
def example(tmp_path):
    closure, source = tmp_path / "closure", tmp_path / "actor"
    closure.mkdir()
    source.mkdir()
    rows = []
    for name, role, requires in (("check.txt", "scorer", ["runtime.txt"]),
                                 ("runtime.txt", "runtime", ["package.txt"]),
                                 ("package.txt", "dependency", [])):
        raw = f"synthetic {role} metadata; never executed\n".encode()
        (closure / name).write_bytes(raw)
        if role == "runtime":
            (closure / name).chmod(0o755)
        rows.append(dict(path=name, role=role, requires=requires,
                         bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    (source / "results").mkdir()
    (source / "results" / "answer.txt").write_bytes(b"synthetic data")
    # Unselected actor content must never be scanned or copied.
    (source / "private.txt").write_text("unselected synthetic data")
    (source / "unselected-link").symlink_to("missing")
    contract = dict(schema="harnessgrad.artifact-scorer.v1", task_id="synthetic-terminal",
                    source_sha256="a" * 64, runtime_image_id="sha256:" + "b" * 64,
                    scorer=dict(entrypoint="check.txt", files=rows,
                                argv=["/control-scorer/runtime.txt", "/control-scorer/check.txt"]),
                    export=dict(files=[dict(path="results/answer.txt", max_bytes=16)],
                                max_bytes=16, max_inodes=3),
                    quota={"/tmp": dict(bytes=65536, inodes=32),
                           "/control-output": dict(bytes=4096, inodes=8)})
    return contract, closure, source, tmp_path / "export"


def project(example):
    contract, closure, source, destination = example
    raw, pin = seal(contract)
    receipt = artifacts.export_state(source, destination, raw, pin, run_id="synthetic-run-1")
    return raw, pin, receipt


def observation(example):
    contract, closure, _, destination = example
    raw, pin = seal(contract)
    return dict(Image=contract["runtime_image_id"], State=dict(Status="created", Running=False),
                Config=dict(Volumes={}, User=artifacts.scorer_user(), Entrypoint=contract["scorer"]["argv"][:1],
                            Cmd=contract["scorer"]["argv"][1:], Env=list(artifacts.SCORER_ENV),
                            WorkingDir="/tmp"), NetworkSettings=dict(Networks={"none": {}}),
                HostConfig=dict(NetworkMode="none", ReadonlyRootfs=True, Privileged=False,
                                CapDrop=["ALL"], SecurityOpt=["no-new-privileges"],
                                LogConfig=copy.deepcopy(LOG_CONFIG),
                                Tmpfs=artifacts.runtime_tmpfs(raw, pin)),
                Mounts=[dict(Type="bind", Source=str(root), Destination=target, RW=False)
                        for root, target in ((destination, "/control-input"),
                                             (closure, "/control-scorer"))])


def test_pinned_local_transitive_closure_and_bounded_export(example):
    contract, closure, source, destination = example
    raw, pin, receipt = project(example)
    verified = artifacts.verify_scorer_closure(closure, raw, pin)
    assert verified["files"] == 3
    assert verified["scope"] == "local_declared_closure_integrity"
    assert receipt["bytes"] == len(b"synthetic data")
    assert receipt["inodes"] == 3
    assert receipt["files"][0]["path"] == "results/answer.txt"
    assert list(destination.iterdir()) == [destination / "results"]
    assert (destination / "results" / "answer.txt").read_bytes() == b"synthetic data"
    assert (source / "private.txt").exists()
    artifacts.verify_state_export(destination, receipt, raw, pin, run_id="synthetic-run-1")
    runtime = artifacts.require_artifact_runtime(observation(example), raw, pin,
                artifact_root=destination, closure_root=closure,
                export_receipt=receipt, run_id="synthetic-run-1")
    assert runtime["scope"] == "inspected_runtime_contract"
    assert "nr_inodes=8" in runtime["tmpfs"]["/control-output"]


@pytest.mark.parametrize("change", ["unresolved", "unused", "runtime_missing", "duplicate",
                                    "directory_collision", "unsafe_path", "bool_bound", "unknown_field",
                                    "actor_command"])
def test_incomplete_or_unpinned_declarations_are_rejected(example, change):
    contract, _, source, destination = example
    if change == "unresolved":
        contract["scorer"]["files"][0]["requires"].append("not-local.txt")
    elif change == "unused":
        contract["scorer"]["files"][1]["requires"] = []
    elif change == "runtime_missing":
        contract["scorer"]["files"][1]["role"] = "dependency"
    elif change == "duplicate":
        contract["export"]["files"] *= 2
    elif change == "directory_collision":
        contract["export"]["files"].append(dict(path="results", max_bytes=1))
    elif change == "unsafe_path":
        contract["export"]["files"][0]["path"] = "results/../answer.txt"
    elif change == "bool_bound":
        contract["export"]["max_bytes"] = True
    elif change == "actor_command":
        contract["scorer"]["argv"][0] = "/control-input/runtime.txt"
    else:
        contract["dependency_closure_verified"] = True
    raw, pin = seal(contract)
    with pytest.raises(AdmissionError):
        artifacts.export_state(source, destination, raw, pin, run_id="synthetic-run-1")
    assert not destination.exists()


def test_contract_pin_is_external_and_cannot_be_changed_by_a_boolean(example):
    contract, closure, _, _ = example
    raw, pin = seal(contract)
    contract["source_sha256"] = "c" * 64
    changed, _ = seal(contract)
    with pytest.raises(AdmissionError, match="seal"):
        artifacts.verify_scorer_closure(closure, changed, pin)


@pytest.mark.parametrize("change", ["missing", "extra", "altered", "symlink", "hardlink"])
def test_scorer_closure_requires_exact_local_bytes(example, change):
    contract, closure, _, _ = example
    raw, pin = seal(contract)
    package = closure / "package.txt"
    if change == "missing":
        package.unlink()
    elif change == "extra":
        (closure / "extra.txt").write_text("extra synthetic dependency")
    elif change == "altered":
        content = package.read_bytes()
        package.write_bytes(b"X" + content[1:])
    elif change == "symlink":
        package.unlink()
        package.symlink_to("runtime.txt")
    else:
        os.link(package, closure.parent / "package-alias")
    with pytest.raises(AdmissionError):
        artifacts.verify_scorer_closure(closure, raw, pin)


@pytest.mark.parametrize("kind", ["file_symlink", "directory_symlink", "hardlink", "executable", "fifo", "missing"])
def test_state_export_refuses_toolchain_links_special_or_missing_files(example, kind):
    contract, _, source, destination = example
    answer = source / "results" / "answer.txt"
    if kind == "file_symlink":
        answer.unlink()
        answer.symlink_to("../private.txt")
    elif kind == "directory_symlink":
        answer.unlink()
        (source / "results").rmdir()
        (source / "results").symlink_to(source.parent)
    elif kind == "hardlink":
        os.link(answer, source / "alias")
    elif kind == "executable":
        answer.chmod(0o700)
    else:
        answer.unlink()
        if kind == "fifo":
            os.mkfifo(answer)
    raw, pin = seal(contract)
    with pytest.raises(AdmissionError):
        artifacts.export_state(source, destination, raw, pin, run_id="synthetic-run-1")
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["per_file", "total", "inodes"])
def test_export_enforces_both_byte_and_inode_limits(example, kind):
    contract, _, source, destination = example
    if kind == "per_file":
        (source / "results" / "answer.txt").write_bytes(b"x" * 17)
    elif kind == "total":
        (source / "other.txt").write_bytes(b"more")
        contract["export"]["files"].append(dict(path="other.txt", max_bytes=16))
        contract["export"]["max_inodes"] = 4
    else:
        contract["export"]["max_inodes"] = 2
    raw, pin = seal(contract)
    with pytest.raises(AdmissionError, match="quota"):
        artifacts.export_state(source, destination, raw, pin, run_id="synthetic-run-1")
    assert not destination.exists()


def test_existing_projection_is_preserved_and_never_resumed(example):
    contract, _, source, destination = example
    raw, pin, receipt = project(example)
    with pytest.raises(AdmissionError, match="never reused"):
        artifacts.export_state(source, destination, raw, pin, run_id="synthetic-run-2")
    artifacts.verify_state_export(destination, receipt, raw, pin, run_id="synthetic-run-1")
    with pytest.raises(AdmissionError, match="identity"):
        artifacts.verify_state_export(destination, receipt, raw, pin, run_id="synthetic-run-2")


@pytest.mark.parametrize("change", ["modified", "extra", "wrong_task", "wrong_pin", "accounting"])
def test_export_receipt_cannot_mount_modified_or_unrelated_state(example, change):
    _, closure, _, destination = example
    raw, pin, receipt = project(example)
    if change == "modified":
        (destination / "results" / "answer.txt").write_bytes(b"different data")
    elif change == "extra":
        (destination / "runtime.txt").write_text("actor-supplied synthetic runtime")
    elif change == "wrong_task":
        receipt["task_id"] = "other-task"
    elif change == "wrong_pin":
        receipt["contract_sha256"] = "0" * 64
    else:
        receipt["inodes"] = 1
    with pytest.raises(AdmissionError):
        artifacts.require_artifact_runtime(observation(example), raw, pin,
            artifact_root=destination, closure_root=closure,
            export_receipt=receipt, run_id="synthetic-run-1")


@pytest.mark.parametrize("change", ["snapshot", "network", "writable_root", "quota_bytes", "quota_inodes",
                                    "extra_tmpfs", "missing_tmpfs", "writable_bind", "extra_volume", "logs",
                                    "privileged", "capabilities", "actor_closure", "started",
                                    "actor_interpreter", "actor_scorer", "actor_environment", "actor_cwd"])
def test_runtime_requires_pristine_offline_readonly_boundary_and_hard_quotas(example, change):
    _, closure, _, destination = example
    raw, pin, receipt = project(example)
    info = observation(example)
    if change == "snapshot":
        info["Image"] = "sha256:" + "c" * 64
    elif change == "network":
        info["HostConfig"]["NetworkMode"] = "bridge"
    elif change == "writable_root":
        info["HostConfig"]["ReadonlyRootfs"] = False
    elif change.startswith("quota_"):
        key = "size=4096" if change == "quota_bytes" else "nr_inodes=8"
        info["HostConfig"]["Tmpfs"]["/control-output"] = info["HostConfig"]["Tmpfs"]["/control-output"].replace(key, "")
    elif change == "extra_tmpfs":
        info["HostConfig"]["Tmpfs"]["/app"] = "rw"
    elif change == "missing_tmpfs":
        del info["HostConfig"]["Tmpfs"]["/control-output"]
    elif change == "writable_bind":
        info["Mounts"][0]["RW"] = True
    elif change == "extra_volume":
        info["Mounts"].append(dict(Type="volume", Source="extra", Destination="/app", RW=True))
    elif change == "logs":
        info["HostConfig"]["LogConfig"] = {}
    elif change == "privileged":
        info["HostConfig"]["Privileged"] = True
    elif change == "capabilities":
        info["HostConfig"]["CapDrop"] = []
    elif change == "actor_closure":
        info["Mounts"][1]["Source"] = str(example[2])
    elif change == "actor_interpreter":
        info["Config"]["Entrypoint"] = ["/control-input/runtime.txt"]
    elif change == "actor_scorer":
        info["Config"]["Cmd"] = ["/control-input/check.txt"]
    elif change == "actor_environment":
        info["Config"]["Env"].append("PYTHONPATH=/control-input")
    elif change == "actor_cwd":
        info["Config"]["WorkingDir"] = "/control-input"
    else:
        info["State"] = dict(Status="running", Running=True)
    with pytest.raises(AdmissionError):
        artifacts.require_artifact_runtime(info, raw, pin,
            artifact_root=destination, closure_root=closure,
            export_receipt=receipt, run_id="synthetic-run-1")


@pytest.mark.parametrize("replacement,reason", [(b"x" * 100, "quota"), (b"x" * 14, "changed")])
def test_projection_refuses_source_changes_during_export(example, monkeypatch, replacement, reason):
    contract, _, source, destination = example
    raw, pin = seal(contract)
    open_regular = artifacts._regular

    from contextlib import contextmanager

    @contextmanager
    def growing(root, path, **kwargs):
        with open_regular(root, path, **kwargs) as (stream, before):
            answer = source / "results" / "answer.txt"
            answer.write_bytes(replacement)
            # Filesystem timestamp granularity can hide two writes in one tick.
            # Exercise an observable change; quiescence is a caller obligation.
            os.utime(answer, ns=(before.st_atime_ns, before.st_mtime_ns + 10**9))
            yield stream, before

    monkeypatch.setattr(artifacts, "_regular", growing)
    with pytest.raises(AdmissionError, match=reason):
        artifacts.export_state(source, destination, raw, pin, run_id="synthetic-run-1")
    assert not destination.exists()


def test_failed_export_cleanup_preserves_unknown_state_as_admission_error(example, monkeypatch):
    contract, _, source, destination = example
    (source / "results" / "answer.txt").unlink()
    raw, pin = seal(contract)

    def failed_cleanup(path):
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(artifacts.shutil, "rmtree", failed_cleanup)
    with pytest.raises(AdmissionError, match="cleanup.*retained"):
        artifacts.export_state(source, destination, raw, pin, run_id="synthetic-run-1")
    assert destination.exists()
