"""Synthetic facts only: no Docker, subprocess, socket, model or dataset access."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eval.containers import container
from eval.admission import runner_admission as admission

BINDING = dict(adapter_id="synthetic-adapter", run_id="synthetic-run-158",
               trial_id="synthetic-trial-1", manifest_sha256="a" * 64)
RESOURCES = dict(cpu_millis=1000, memory_bytes=256 << 20, swap_bytes=0,
                 pids=64, timeout_ms=30000)


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("synthetic contract attempted real execution")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(container, "docker", refuse)
    monkeypatch.delenv("HG_SNAPSHOT_MAX_BYTES", raising=False)
    monkeypatch.delenv("HG_SNAPSHOT_RESERVE_BYTES", raising=False)


@pytest.fixture
def runtime():
    policy = dict(resources=RESOURCES.copy(), network="none",
                  filesystems={"/work": dict(bytes=4096, inodes=16)})
    actual = dict(binding=BINDING.copy(), evidence_sha256="b" * 64,
                  resources=RESOURCES.copy(), resource_backend="cgroup-v2",
                  timeout_backend="host-supervisor", network="none", attachments=[],
                  read_only_root=True, filesystems={"/work": dict(bytes=4096, inodes=16,
                  backend="tmpfs", scope_id="synthetic-mount-1", evidence_sha256="c" * 64)})
    return policy, actual


@pytest.fixture
def snapshot():
    policy = dict(max_bytes=4096, max_inodes=16, reserve_bytes=1024,
                  reserve_inodes=4, timeout_ms=30000)
    actual = dict(binding=BINDING.copy(), evidence_sha256="b" * 64,
                  writable_bytes=4096, writable_inodes=16, free_bytes=5120,
                  free_inodes=20, running=False, exit_code=0, oom_killed=False,
                  timeout_ms=30000, device_id="synthetic-device-1")
    return policy, actual


@pytest.fixture
def cleanup():
    owned = [dict(kind=kind, id="synthetic-" + kind) for kind in sorted(admission.KINDS)]
    policy = dict(owned=owned, timeout_ms=30000)
    actual = dict(binding=BINDING.copy(), evidence_sha256="b" * 64,
                  objects=[dict(**row, absent=True, evidence_sha256="c" * 64) for row in owned],
                  namespaces={kind: dict(remaining=[], evidence_sha256="d" * 64)
                              for kind in admission.KINDS}, timeout_ms=30000)
    return policy, actual


def refused(gate, policy, actual, code):
    before = deepcopy((policy, actual))
    with pytest.raises(admission.AdmissionError, match=code) as error:
        gate(BINDING, policy, actual)
    receipt = error.value.receipt
    assert receipt["status"] == "blocked" and receipt["measured"] is False
    assert receipt["production_authorized"] is False and "score" not in receipt
    assert len(json.dumps(receipt).encode()) <= admission.MAX_RECEIPT_BYTES
    assert (policy, actual) == before
    return receipt


@pytest.mark.parametrize("stage", ["runtime", "snapshot", "cleanup"])
def test_valid_synthetic_contract_is_deterministic_bounded_and_not_authorization(request, stage):
    policy, actual = request.getfixturevalue(stage)
    gate = getattr(admission, "admit_" + stage)
    before = deepcopy((policy, actual))
    receipt = gate(BINDING, policy, actual)
    assert receipt == gate(BINDING, policy, actual)
    assert receipt["status"] == "passed" and receipt["measured"] is False
    assert receipt["production_authorized"] is False and "score" not in receipt
    assert len(json.dumps(receipt).encode()) <= admission.MAX_RECEIPT_BYTES
    assert len(receipt["policy_sha256"]) == len(receipt["observation_sha256"]) == 64
    assert (policy, actual) == before
    if stage == "cleanup":
        assert receipt["lease_action"] == "release"


@pytest.mark.parametrize("stage", ["runtime", "snapshot", "cleanup"])
@pytest.mark.parametrize("key", list(BINDING))
def test_receipts_cannot_be_reused_for_another_binding(request, stage, key):
    policy, actual = request.getfixturevalue(stage)
    actual["binding"][key] = "f" * 64 if key.endswith("sha256") else "another-synthetic-identity"
    refused(getattr(admission, "admit_" + stage), policy, actual, "stale_observation")


@pytest.mark.parametrize("backend", ["declared", "reservation-ledger", "bind", "overlay", "unknown"])
def test_accounting_and_writable_binds_do_not_prove_quota_enforcement(runtime, backend):
    policy, actual = runtime
    actual["filesystems"]["/work"]["backend"] = backend
    refused(admission.admit_runtime, policy, actual, "filesystem_enforcement_unproven")


def test_declared_quota_boolean_cannot_override_missing_backend(runtime):
    policy, actual = runtime
    actual["filesystems"]["/work"] = dict(bytes=4096, inodes=16, enforced=True)
    refused(admission.admit_runtime, policy, actual, "filesystem_enforcement_unproven")


def test_project_quota_observations_use_the_same_byte_and_inode_contract(runtime):
    policy, actual = runtime
    actual["filesystems"]["/work"]["backend"] = "project-quota"
    assert admission.admit_runtime(BINDING, policy, actual)["status"] == "passed"


@pytest.mark.parametrize("key", ["bytes", "inodes"])
@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "4096", None, 2**64])
def test_both_quota_dimensions_require_actual_finite_positive_limits(runtime, key, bad):
    policy, actual = runtime
    actual["filesystems"]["/work"][key] = bad
    refused(admission.admit_runtime, policy, actual, "invalid_filesystem_observation")


@pytest.mark.parametrize("key", ["bytes", "inodes"])
def test_quota_limits_must_match_the_frozen_policy(runtime, key):
    policy, actual = runtime
    actual["filesystems"]["/work"][key] += 1
    refused(admission.admit_runtime, policy, actual, "filesystem_limit_mismatch")


@pytest.mark.parametrize("change", ["missing", "extra", "shared"])
def test_all_writable_surfaces_need_distinct_bounded_scopes(runtime, change):
    policy, actual = runtime
    if change == "missing":
        actual["filesystems"] = {}
    else:
        actual["filesystems"]["/other"] = deepcopy(actual["filesystems"]["/work"])
        if change == "shared":
            policy["filesystems"]["/other"] = dict(bytes=4096, inodes=16)
    refused(admission.admit_runtime, policy, actual,
            "shared_quota_scope" if change == "shared" else "writable_surface_inventory_mismatch")


@pytest.mark.parametrize("path", ["relative", "/", "//work", "/work/../other", "/work/", "/work\n"])
def test_surface_paths_are_canonical(runtime, path):
    policy, actual = runtime
    policy["filesystems"] = {path: dict(bytes=4096, inodes=16)}
    refused(admission.admit_runtime, policy, actual, "invalid_surface_path")


@pytest.mark.parametrize("key,bad,reason", [
    ("network", "host", "network_enforcement_unproven"),
    ("network", "bridge", "network_enforcement_unproven"),
    ("attachments", ["synthetic-bridge"], "network_enforcement_unproven"),
    ("read_only_root", False, "writable_root_unbounded"),
    ("read_only_root", 1, "writable_root_unbounded"),
    ("resource_backend", "declared", "resource_enforcement_unproven"),
    ("timeout_backend", "declared", "deadline_enforcement_unproven"),
])
def test_runtime_refuses_missing_isolation_and_enforcement(runtime, key, bad, reason):
    policy, actual = runtime
    actual[key] = bad
    refused(admission.admit_runtime, policy, actual, reason)


@pytest.mark.parametrize("key,bad", [
    (key, bad) for key in sorted(admission.RESOURCE_KEYS)
    for bad in [-1, True, float("inf"), "64", None, 2**64]
    if not (key == "pids" and bad is None)
])
def test_resources_reject_invalid_policy_and_observations(runtime, key, bad):
    policy, actual = runtime
    actual["resources"][key] = bad
    refused(admission.admit_runtime, policy, actual, "invalid_resource_observation")
    actual["resources"][key] = RESOURCES[key]
    policy["resources"][key] = bad
    refused(admission.admit_runtime, policy, actual, "invalid_resource_policy")


@pytest.mark.parametrize("actual_pids", [None, 48, 2048])
def test_undeclared_pid_cap_records_actual_host_ceiling(runtime, actual_pids):
    policy, actual = runtime
    policy["resources"]["pids"] = None
    actual["resources"]["pids"] = actual_pids
    receipt = admission.admit_runtime(BINDING, policy, actual)
    assert receipt["evidence"]["resources"]["pids"] == actual_pids
    assert policy["resources"]["pids"] is None


def test_declared_pid_cap_cannot_admit_unlimited_observation(runtime):
    policy, actual = runtime
    actual["resources"]["pids"] = None
    refused(admission.admit_runtime, policy, actual, "resource_limit_mismatch")


@pytest.mark.parametrize("key", sorted(admission.RESOURCE_KEYS))
def test_each_resource_limit_is_reconciled_with_policy(runtime, key):
    policy, actual = runtime
    actual["resources"][key] += 1
    refused(admission.admit_runtime, policy, actual, "resource_limit_mismatch")


@pytest.mark.parametrize("key", ["writable_bytes", "writable_inodes", "free_bytes", "free_inodes"])
@pytest.mark.parametrize("bad", [-1, True, float("nan"), "4"])
def test_snapshot_quantities_cannot_fake_headroom(snapshot, key, bad):
    policy, actual = snapshot
    actual[key] = bad
    refused(admission.admit_snapshot, policy, actual, "invalid_snapshot_observation")


@pytest.mark.parametrize("unit", ["bytes", "inodes"])
def test_snapshot_size_and_headroom_boundaries(snapshot, unit):
    policy, actual = snapshot
    actual["free_" + unit] -= 1
    refused(admission.admit_snapshot, policy, actual, "snapshot_headroom_insufficient")
    actual["free_" + unit] += 2
    actual["writable_" + unit] += 1
    refused(admission.admit_snapshot, policy, actual, "snapshot_size_exceeded")


@pytest.mark.parametrize("key,bad,reason", [
    ("running", True, "snapshot_quiescence_unproven"),
    ("exit_code", 137, "snapshot_quiescence_unproven"),
    ("exit_code", False, "snapshot_quiescence_unproven"),
    ("oom_killed", True, "snapshot_quiescence_unproven"),
    ("timeout_ms", 0, "invalid_snapshot_deadline"),
    ("timeout_ms", 30001, "snapshot_deadline_mismatch"),
])
def test_snapshot_failure_or_unbounded_deadline_is_unmeasured(snapshot, key, bad, reason):
    policy, actual = snapshot
    actual[key] = bad
    refused(admission.admit_snapshot, policy, actual, reason)


@pytest.mark.parametrize("key", ["max_bytes", "max_inodes", "reserve_bytes", "reserve_inodes", "timeout_ms"])
@pytest.mark.parametrize("bad", [0, True, float("inf"), None])
def test_snapshot_policy_requires_finite_positive_budgets(snapshot, key, bad):
    policy, actual = snapshot
    policy[key] = bad
    refused(admission.admit_snapshot, policy, actual, "invalid_snapshot_policy")


@pytest.mark.parametrize("stage", ["runtime", "snapshot", "cleanup"])
def test_missing_evidence_hash_is_not_trusted_evidence(request, stage):
    policy, actual = request.getfixturevalue(stage)
    actual["evidence_sha256"] = ""
    refused(getattr(admission, "admit_" + stage), policy, actual, "invalid_evidence_hash")


@pytest.mark.parametrize("bad", [0, True, 30001])
def test_cleanup_requires_the_bound_deadline(cleanup, bad):
    policy, actual = cleanup
    actual["timeout_ms"] = bad
    receipt = refused(admission.admit_cleanup, policy, actual, "cleanup_deadline_mismatch")
    assert receipt["lease_action"] == "quarantine"


@pytest.mark.parametrize("kind", sorted(admission.KINDS))
@pytest.mark.parametrize("change", ["present", "namespace", "identity", "duplicate"])
def test_known_bad_cleanup_observations_retain_capacity(cleanup, kind, change):
    policy, actual = cleanup
    row = next(row for row in actual["objects"] if row["kind"] == kind)
    reasons = dict(present="owned_absence_unproven", namespace="owned_namespace_absence_unproven",
                   identity="cleanup_inventory_mismatch", duplicate="duplicate_owned_resource")
    if change == "present":
        row["absent"] = False
    elif change == "namespace":
        actual["namespaces"][kind]["remaining"] = ["synthetic-unrecorded-object"]
    elif change == "identity":
        row["id"] = "synthetic-other-object"
    else:
        actual["objects"].append(deepcopy(row))
    receipt = refused(admission.admit_cleanup, policy, actual, reasons[change])
    assert receipt["lease_action"] == "quarantine"


@pytest.mark.parametrize("stage,change,reason", [
    ("snapshot", "running", "snapshot_quiescence_unproven"),
    ("snapshot", "exit_code", "snapshot_quiescence_unproven"),
    ("snapshot", "oom_killed", "snapshot_quiescence_unproven"),
    ("snapshot", "timeout_ms", "snapshot_deadline_mismatch"),
    ("snapshot", "writable_bytes", "snapshot_size_exceeded"),
    ("snapshot", "writable_inodes", "snapshot_size_exceeded"),
    ("snapshot", "free_bytes", "snapshot_headroom_insufficient"),
    ("snapshot", "free_inodes", "snapshot_headroom_insufficient"),
    ("cleanup", "present", "owned_absence_unproven"),
    ("cleanup", "unidentified_present", "owned_absence_unproven"),
    ("cleanup", "namespace", "owned_namespace_absence_unproven"),
    ("cleanup", "identity", "cleanup_inventory_mismatch"),
    ("cleanup", "partial_identity", "cleanup_inventory_mismatch"),
    ("cleanup", "duplicate", "duplicate_owned_resource"),
    ("cleanup", "timeout_ms", "cleanup_deadline_mismatch"),
])
def test_known_bad_lifecycle_observation_is_refused_despite_incomplete_evidence(
        request, stage, change, reason):
    policy, complete = request.getfixturevalue(stage)
    # Start with *only* the binding. Coverage gaps must never hide a real failure.
    actual = dict(binding=BINDING.copy())
    if stage == "snapshot":
        if change in ("running", "oom_killed"):
            actual[change] = True
        elif change == "exit_code":
            actual[change] = 137
        elif change == "timeout_ms":
            actual[change] = policy[change] + 1
        elif change.startswith("writable_"):
            actual[change] = policy[change.replace("writable_", "max_")] + 1
        else:
            # An observed reserve shortfall is bad even with size unknown.
            actual[change] = policy[change.replace("free_", "reserve_")] - 1
    elif change == "timeout_ms":
        actual[change] = policy[change] + 1
    elif change == "namespace":
        actual["namespaces"] = dict(container=dict(remaining=["synthetic-survivor"]))
    else:
        row = deepcopy(complete["objects"][0])
        row.pop("evidence_sha256")
        actual["objects"] = [row]
        if change in ("present", "unidentified_present"):
            row["absent"] = False
            if change == "unidentified_present":
                del row["kind"], row["id"]
        elif change in ("identity", "partial_identity"):
            row["id"] = "synthetic-other-owner"
            if change == "partial_identity":
                del row["kind"]
        else:
            actual["objects"].append(deepcopy(row))
    receipt = refused(getattr(admission, "admit_" + stage), policy, actual, reason)
    assert receipt["reason"] == reason
    if stage == "cleanup":
        assert receipt["lease_action"] == "quarantine"


@pytest.mark.parametrize("stage,key", [
    ("snapshot", key) for key in (
        "writable_bytes", "writable_inodes", "free_bytes", "free_inodes", "running",
        "exit_code", "oom_killed", "timeout_ms", "device_id", "evidence_sha256", "all")
] + [
    ("cleanup", key) for key in (
        "objects", "namespaces", "timeout_ms", "evidence_sha256", "all", "owned[0]",
        "objects[0].kind", "objects[0].id", "objects[0].absent",
        "objects[0].evidence_sha256", "namespaces.container",
        "namespaces.container.remaining", "namespaces.container.evidence_sha256")
])
@pytest.mark.parametrize("unknown", ["missing", "null"])
def test_incomplete_lifecycle_observation_proceeds_with_uncertainty(request, stage, key, unknown):
    policy, actual = request.getfixturevalue(stage)
    target, field = actual, key
    if key == "all":
        actual = dict(binding=BINDING.copy())
        if unknown == "null":
            actual.update({name: None for name in target if name != "binding"})
    elif key == "owned[0]":
        actual["objects"].pop(0)
    else:
        if key.startswith("objects[0]."):
            target, field = actual["objects"][0], key.split(".", 1)[1]
        elif key.startswith("namespaces.container."):
            target, field = actual["namespaces"]["container"], key.rsplit(".", 1)[1]
        elif key == "namespaces.container":
            target, field = actual["namespaces"], "container"
        if unknown == "missing":
            del target[field]
        else:
            target[field] = None
    before = deepcopy((policy, actual))
    gate = getattr(admission, "admit_" + stage)
    receipt = gate(BINDING, policy, actual)
    assert receipt == gate(BINDING, policy, actual)
    assert (policy, actual) == before
    assert receipt["status"] == "passed"
    assert receipt["measured"] is False and receipt["production_authorized"] is False
    evidence = receipt["evidence"]
    assert evidence["best_effort"] is True
    assert evidence["uncertainties"]
    assert (key if key != "all" else "evidence_sha256") in evidence["uncertainties"]
    assert len(json.dumps(receipt).encode()) <= admission.MAX_RECEIPT_BYTES
    if stage == "cleanup":
        assert receipt["lease_action"] == "release"
        if key in ("namespaces", "namespaces.container", "namespaces.container.remaining", "all"):
            assert "container" not in evidence["checked_kinds"]
        if key in ("timeout_ms", "all"):
            assert evidence["timeout_ms"] is None  # Do not echo policy as an observation.
    elif key in ("writable_bytes", "writable_inodes", "all"):
        unit = "inodes" if key.endswith("inodes") else "bytes"
        assert evidence["required_" + unit] is None


@pytest.mark.parametrize("stage", ["runtime", "snapshot", "cleanup"])
def test_large_or_private_payloads_are_not_echoed_into_receipts(request, stage):
    policy, actual = request.getfixturevalue(stage)
    actual["private_log"] = "synthetic-private-sentinel" * 1000
    receipt = refused(getattr(admission, "admit_" + stage), policy, actual, "invalid_observation")
    assert "synthetic-private-sentinel" not in json.dumps(receipt)


def test_total_receipt_and_inventory_bounds(runtime, cleanup):
    policy, actual = runtime
    for index in range(admission.MAX_SURFACES):
        path = "/" + "s" * 230 + str(index)
        policy["filesystems"][path] = dict(bytes=4096, inodes=16)
        actual["filesystems"][path] = dict(bytes=4096, inodes=16, backend="tmpfs",
            scope_id="scope-" + "s" * 220 + str(index), evidence_sha256="c" * 64)
    refused(admission.admit_runtime, policy, actual, "invalid_surfaces")
    del policy["filesystems"]["/work"], actual["filesystems"]["/work"]
    refused(admission.admit_runtime, policy, actual, "receipt_too_large")
    policy, actual = cleanup
    policy["owned"] = [dict(kind="workspace", id=str(i)) for i in range(admission.MAX_OWNED + 1)]
    refused(admission.admit_cleanup, policy, actual, "invalid_owned_inventory")


@pytest.mark.parametrize("key", ["HG_SNAPSHOT_MAX_BYTES", "HG_SNAPSHOT_RESERVE_BYTES"])
@pytest.mark.parametrize("bad", ["-1", "0", "invalid", "1.5", str(2**64)])
def test_legacy_snapshot_invalid_limits_refuse_before_any_docker_call(monkeypatch, key, bad):
    monkeypatch.setenv(key, bad)
    with pytest.raises(container.SnapshotUnavailable, match="invalid " + key):
        container.snapshot_admission("synthetic-container")


@pytest.mark.parametrize("size", ["-1", "invalid", str(2**64)])
def test_legacy_snapshot_invalid_size_does_not_reach_commit(monkeypatch, size):
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        assert args[:2] == ("inspect", "--size")
        return subprocess.CompletedProcess(args, 0, size, "")
    monkeypatch.setattr(container, "docker", fake)
    with pytest.raises(container.SnapshotUnavailable, match="invalid writable size"):
        container.commit("synthetic-container", "synthetic-snapshot")
    assert len(calls) == 1


def legacy_snapshot(monkeypatch, *, size="1024", root="/synthetic/docker", free=2048):
    calls = []
    monkeypatch.setenv("HG_SNAPSHOT_MAX_BYTES", "1024")
    monkeypatch.setenv("HG_SNAPSHOT_RESERVE_BYTES", "1024")
    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("inspect", "--size"):
            return subprocess.CompletedProcess(args, 0, size, "")
        assert args[0] == "info"
        return subprocess.CompletedProcess(args, 0, root, "")
    monkeypatch.setattr(container, "docker", fake)
    monkeypatch.setattr(container.shutil, "disk_usage", lambda path: SimpleNamespace(free=free))
    return calls


def test_legacy_snapshot_receipt_records_bounds_without_claiming_quota(monkeypatch):
    calls = legacy_snapshot(monkeypatch)
    receipt = container.snapshot_admission("synthetic-container")
    assert receipt == dict(writable_bytes=1024, free_bytes=2048, required_bytes=2048,
        docker_root="/synthetic/docker", max_bytes=1024, reserve_bytes=1024,
        inspect_timeout_s=60, commit_timeout_s=1800, preflight_only=True, quota_enforced=False)
    assert all(options["timeout"] == receipt["inspect_timeout_s"] for _, options in calls)


@pytest.mark.parametrize("root,free", [("", 2048), ("relative", 2048), ("/synthetic/docker", -1),
                                      ("/synthetic/docker", 2047)])
def test_legacy_snapshot_refuses_unknown_root_or_bad_headroom(monkeypatch, root, free):
    legacy_snapshot(monkeypatch, root=root, free=free)
    with pytest.raises(container.SnapshotUnavailable):
        container.snapshot_admission("synthetic-container")


@pytest.mark.parametrize("stage", ["inspect", "info"])
@pytest.mark.parametrize("error", [OSError("synthetic failure"), subprocess.TimeoutExpired("synthetic", 60)])
def test_legacy_snapshot_query_failure_is_explicit_infrastructure_failure(monkeypatch, stage, error):
    def fake(*args, **kwargs):
        if args[0] == stage:
            raise error
        return subprocess.CompletedProcess(args, 0, "0", "")
    monkeypatch.setattr(container, "docker", fake)
    with pytest.raises(container.SnapshotUnavailable, match="snapshot admission"):
        container.snapshot_admission("synthetic-container")
