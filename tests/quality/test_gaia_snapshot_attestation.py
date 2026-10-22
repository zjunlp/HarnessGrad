"""Synthetic, offline compatibility/admission regressions; no GAIA lifecycle evidence."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from eval.containers import container
from eval.policy import gaia_snapshot_attestation as attestation


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline test attempted subprocess, network or host disk inspection")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(container.shutil, "disk_usage", forbidden)
    monkeypatch.delenv("HG_SNAPSHOT_MAX_BYTES", raising=False)
    monkeypatch.delenv("HG_SNAPSHOT_RESERVE_BYTES", raising=False)


def source():
    return {"schema": attestation.SOURCE_SCHEMA, "commit": "a" * 40, "tree": "b" * 40,
            "files": {p: {"mode": "100644", "oid": "c" * 40}
                      for p in attestation.REQUIRED_SOURCE}}


def runtime():
    return {"schema": attestation.RUNTIME_SCHEMA, "dataset_revision": "d" * 40,
            "split": "validation", "image_id": "sha256:" + "e" * 64,
            "verifier_image_id": "sha256:" + "f" * 64,
            "container_plan": 1, "snapshot_max_bytes": 16 << 30,
            "snapshot_reserve_bytes": 1 << 30,
            "limits": {"cpus": 2, "memory_mb": 4096, "pids": 256},
            **{key: "1" * 64 for key in attestation.RUNTIME_HASHES}}


def compare(candidate=None, binding=None, **kwargs):
    return attestation.attest_compatibility(
        source(), candidate or source(), runtime(), binding or runtime(), **kwargs)


def test_equal_source_and_runtime_never_authorize_lifecycle_or_receipt_reuse():
    for receipt in (None, "2" * 64):
        report = compare(reference_receipt_sha256=receipt)
        assert report["compatible"] is True
        assert report["evidence_reusable"] is False
        assert report["lifecycle_authorized"] is False
        assert report["receipt_validation"] == "not-performed"
        assert report["reference_receipt_sha256"] == receipt
        assert "blocked" in report["lifecycle_status"]
        assert report["reference_source_sha256"] == report["candidate_source_sha256"]


@pytest.mark.parametrize("path", sorted(attestation.REVIEW_ONLY))
def test_exact_review_only_addition_is_recorded_without_changing_runtime(path):
    candidate = source()
    candidate["commit"] = "2" * 40
    candidate["tree"] = "3" * 40
    candidate["files"][path] = {"mode": "100644", "oid": "4" * 40}
    report = compare(candidate)
    assert report["compatible"] is True
    assert report["changed_paths"] == [path]
    assert report["runtime_changed_paths"] == []
    assert report["reference_source_sha256"] != report["candidate_source_sha256"]


@pytest.mark.parametrize("path", [
    "eval/container.py", "eval/container_state.py", "data/datasets/gaia/verify/check.py",
    "eval/gaia_snapshot_attestation.py", "docs/runtime-input.md",
    "tests/runtime_control.py", "data/datasets/gaia/cli/ask", "unknown-runtime.txt",
])
def test_any_nonexempt_changed_or_added_path_requires_fresh_controls(path):
    candidate = source()
    candidate["files"][path] = {"mode": "100644", "oid": "5" * 40}
    report = compare(candidate)
    assert report["compatible"] is False
    assert report["runtime_changed_paths"] == [path]


def test_deletion_and_mode_changes_are_not_hidden_by_equal_blob_ids():
    reference = source()
    reference["files"]["optional.py"] = {"mode": "100644", "oid": "c" * 40}
    candidate = deepcopy(reference)
    del candidate["files"]["optional.py"]
    candidate["files"]["eval/container.py"]["mode"] = "100755"
    report = attestation.attest_compatibility(reference, candidate, runtime(), runtime())
    assert report["runtime_changed_paths"] == ["eval/container.py", "optional.py"]
    assert report["compatible"] is False


@pytest.mark.parametrize("key", sorted(attestation.RUNTIME_KEYS - {"schema", "split"}))
def test_every_external_runtime_binding_change_blocks_compatibility(key):
    binding = runtime()
    if key == "limits":
        binding[key]["memory_mb"] += 1
    elif isinstance(binding[key], int):
        binding[key] += 1
    elif key.endswith("image_id"):
        binding[key] = "sha256:" + "9" * 64
    else:
        binding[key] = "9" * len(binding[key])
    report = compare(binding=binding)
    assert report["compatible"] is False
    assert report["binding_changed_fields"] == [key]


@pytest.mark.parametrize("key,value", [
    ("split", "test"), ("image_id", "harnessgrad/gaia:latest"),
    ("snapshot_max_bytes", True), ("snapshot_reserve_bytes", -1),
    ("container_plan", 0), ("pool_sha256", "unknown"),
    ("limits", {"cpus": 2, "memory_mb": 4096}),
    ("limits", {"cpus": 2, "memory_mb": 4096, "pids": False}),
])
def test_incomplete_or_unpinned_runtime_cannot_generate_attestation(key, value):
    binding = runtime()
    binding[key] = value
    with pytest.raises(attestation.CompatibilityError, match="runtime binding"):
        compare(binding=binding)


@pytest.mark.parametrize("key", sorted(attestation.RUNTIME_KEYS))
def test_missing_runtime_field_is_not_defaulted(key):
    binding = runtime()
    del binding[key]
    with pytest.raises(attestation.CompatibilityError):
        compare(binding=binding)


@pytest.mark.parametrize("path", ["../file", "/file", "a/../file", "a//file", "a\\file", "x\nfile"])
def test_invalid_source_paths_are_rejected(path):
    candidate = source()
    candidate["files"][path] = {"mode": "100644", "oid": "c" * 40}
    with pytest.raises(attestation.CompatibilityError):
        compare(candidate)


@pytest.mark.parametrize("mode", ["100755", "120000"])
def test_review_only_path_cannot_exempt_executable_or_symlink(mode):
    candidate = source()
    candidate["files"]["reports/GAIA_GATE.md"] = {"mode": mode, "oid": "c" * 40}
    with pytest.raises(attestation.CompatibilityError, match="nonexecutable"):
        compare(candidate)


def test_missing_critical_source_and_invalid_receipt_digest_are_rejected():
    candidate = source()
    del candidate["files"]["eval/container_state.py"]
    with pytest.raises(attestation.CompatibilityError, match="source identity"):
        compare(candidate)
    with pytest.raises(attestation.CompatibilityError, match="receipt digest"):
        compare(reference_receipt_sha256="reports/report.md")


def test_attestation_is_deterministic_and_does_not_mutate_inputs():
    reference, candidate, binding = source(), source(), runtime()
    before = deepcopy((reference, candidate, binding))
    first = attestation.attest_compatibility(reference, candidate, binding, binding)
    candidate["files"] = dict(reversed(list(candidate["files"].items())))
    second = attestation.attest_compatibility(reference, candidate, binding, binding)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert (reference, candidate, binding) == before


def git_stub(monkeypatch, *, status="", head="a" * 40, records=None, object_type="commit"):
    calls = []
    if records is None:
        records = "".join(f"{e['mode']} blob {e['oid']}\t{p}\0"
                          for p, e in source()["files"].items())
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:2] == ["git", "--no-replace-objects"]
        # `errors="replace"`: git metadata can carry a path that is not valid UTF-8, and the
        # attestation's own decode must not end a run. Pinned here so the decoding policy is part
        # of the call shape this stub enforces, not an extra the stub tolerates.
        assert kwargs == {"check": True, "capture_output": True, "text": True,
                          "errors": "replace", "timeout": 30}
        args = argv[6:]
        if args == ["rev-parse", "HEAD"]:
            out = head + "\n"
        elif args == ["status", "--porcelain=v1", "--untracked-files=all"]:
            out = status
        elif args == ["cat-file", "-t", "a" * 40]:
            out = object_type + "\n"
        elif args == ["rev-parse", "a" * 40 + "^{tree}"]:
            out = "b" * 40 + "\n"
        elif args == ["ls-tree", "-rz", "--full-tree", "a" * 40]:
            out = records
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_source_collection_uses_only_git_metadata_and_checks_clean_checkout(monkeypatch):
    calls = git_stub(monkeypatch)
    assert attestation.collect_source(Path("."), "a" * 40, require_checkout=True) == source()
    assert len(calls) == 5
    assert all("show" not in call and "-p" not in call for call in calls)


def test_tree_object_cannot_be_mislabeled_as_a_frozen_commit(monkeypatch):
    git_stub(monkeypatch, object_type="tree")
    with pytest.raises(attestation.CompatibilityError, match="commit object"):
        attestation.collect_source(Path("."), "a" * 40)


@pytest.mark.parametrize("status,head", [(" M eval/container.py\n", "a" * 40),
                                            ("?? local.py\n", "a" * 40), ("", "b" * 40)])
def test_candidate_collection_rejects_dirty_or_wrong_checkout(monkeypatch, status, head):
    git_stub(monkeypatch, status=status, head=head)
    with pytest.raises(attestation.CompatibilityError):
        attestation.collect_source(Path("."), "a" * 40, require_checkout=True)


@pytest.mark.parametrize("records", ["bad-record\0", "160000 commit " + "c" * 40 + "\tdep\0"])
def test_source_collection_rejects_malformed_or_unbound_tree(monkeypatch, records):
    git_stub(monkeypatch, records=records)
    with pytest.raises(attestation.CompatibilityError):
        attestation.collect_source(Path("."), "a" * 40)


@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("git", 30)])
def test_source_collection_failure_is_a_refusal(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(attestation.CompatibilityError, match="metadata unavailable"):
        attestation.collect_source(Path("."), "a" * 40)


def admission_stub(monkeypatch, *, writable="10", root="/synthetic/docker", free=100):
    calls = []
    monkeypatch.setenv("HG_SNAPSHOT_MAX_BYTES", "20")
    monkeypatch.setenv("HG_SNAPSHOT_RESERVE_BYTES", "5")
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "inspect":
            out = writable
        elif args[0] == "info":
            out = root
        else:
            raise AssertionError("admission must not commit or start: " + repr(args))
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(container, "docker", docker)
    monkeypatch.setattr(container.shutil, "disk_usage", lambda path: SimpleNamespace(free=free))
    return calls


def test_snapshot_admission_exact_headroom_boundary_and_no_mutation(monkeypatch):
    calls = admission_stub(monkeypatch, writable="20", free=25)
    result = container.snapshot_admission("synthetic-container")
    assert result == {"writable_bytes": 20, "free_bytes": 25, "required_bytes": 25,
                      "docker_root": "/synthetic/docker", "max_bytes": 20,
                      "reserve_bytes": 5, "inspect_timeout_s": 60,
                      "commit_timeout_s": 1800, "preflight_only": True,
                      "quota_enforced": False}
    assert [call[0] for call in calls] == ["inspect", "info"]


@pytest.mark.parametrize("writable,free,root", [
    ("21", 100, "/synthetic/docker"), ("20", 24, "/synthetic/docker"),
    ("-1", 100, "/synthetic/docker"), ("not-known", 100, "/synthetic/docker"),
    ("10", 100, ""), ("10", 100, "relative/docker"),
])
def test_bad_snapshot_observations_are_refused(monkeypatch, writable, free, root):
    admission_stub(monkeypatch, writable=writable, free=free, root=root)
    with pytest.raises(container.SnapshotUnavailable):
        container.snapshot_admission("synthetic-container")


@pytest.mark.parametrize("stage", ["inspect", "info"])
@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("docker", 60)])
def test_admission_observation_exceptions_remain_snapshot_failures(monkeypatch, stage, error):
    admission_stub(monkeypatch)
    def docker(*args, **kwargs):
        if args[0] == stage:
            raise error
        return subprocess.CompletedProcess(args, 0, "10", "")
    monkeypatch.setattr(container, "docker", docker)
    with pytest.raises(container.SnapshotUnavailable, match="admission.*kind=docker"):
        container.snapshot_admission("synthetic-container")


def test_admission_failure_prevents_commit(monkeypatch):
    calls = admission_stub(monkeypatch, writable="-1")
    with pytest.raises(container.SnapshotUnavailable):
        container.commit("synthetic-container", "hg-snapshot:synthetic")
    assert [call[0] for call in calls] == ["inspect"]


@pytest.mark.parametrize("failure_stage", ["stop", "admission", "commit"])
def test_runner_snapshot_failures_are_invalid_and_attempt_owned_teardown(
        tmp_path, monkeypatch, failure_stage):
    from eval.containers import container_state

    repo = tmp_path / "synthetic-harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "agent.py"}))
    events = []
    temporary_directory = container_state.tempfile.TemporaryDirectory
    monkeypatch.setattr(container_state.tempfile, "TemporaryDirectory",
                        lambda **kw: temporary_directory(dir=tmp_path, **kw))
    monkeypatch.setattr(container_state.services_mod, "create_network",
                        lambda *a: "synthetic-network")
    monkeypatch.setattr(container_state.services_mod, "remove_network",
                        lambda *a: events.append("remove-network"))
    monkeypatch.setattr(container, "image_python", lambda *a: "/synthetic/python")
    monkeypatch.setattr(container, "container_env", lambda *a, **kw: {})
    monkeypatch.setattr(container_state.harness_runtime, "lookup", lambda *a, **kw: None)
    monkeypatch.setattr(container_state.harness_runtime, "record", lambda *a, **kw: {})
    for operation in ("create", "start", "mkdir_in", "cp_in", "remove_container", "remove_image"):
        monkeypatch.setattr(container, operation,
                            lambda *a, _operation=operation, **kw: events.append(_operation))
    monkeypatch.setattr(container, "exec_in",
                        lambda *a, **kw: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(container_state, "_verify_container_state",
                        lambda *a, **kw: events.append("verify"))

    def stop(*args):
        events.append("stop")
        if failure_stage == "stop":
            raise container.SnapshotUnavailable("synthetic stop refusal")
    monkeypatch.setattr(container, "stop", stop)
    admission_stub(monkeypatch)

    def docker(*args, **kwargs):
        events.append(args[0])
        if args[0] == "inspect":
            if failure_stage == "admission":
                raise subprocess.TimeoutExpired("synthetic inspect", 60)
            return subprocess.CompletedProcess(args, 0, "10", "")
        if args[0] == "info":
            return subprocess.CompletedProcess(args, 0, "/synthetic/docker", "")
        if args[0] == "commit":
            return subprocess.CompletedProcess(args, 137, "", "synthetic oom")
        raise AssertionError(args)
    monkeypatch.setattr(container, "docker", docker)
    result = container_state._run_one_container_state(
        repo, {"task_id": "synthetic/0", "goal": "Synthetic offline fixture"},
        env_spec={"image": "sha256:" + "a" * 64}, setup=None, verifier=None,
        timeout_s=1, verify_timeout_s=1, run_id="synthetic-offline-only", run_seed="0",
        recordings_root=None)
    assert result["invalid"]["stage"] == "snapshot"
    assert "score" not in result and "verdict" not in result
    assert "verify" not in events and "remove_image" not in events
    assert events[-2:] == ["remove_container", "remove-network"]
    assert "stop" in events
    if failure_stage == "admission":
        assert "commit" not in events
    if failure_stage == "commit":
        assert events.index("stop") < events.index("inspect") < events.index("commit")
