"""Offline GAIA source/runtime compatibility, never lifecycle authorization.

Git tree metadata is sufficient to compare committed source without opening task,
model, or run artifacts. External inputs must be supplied as reviewed digests.
This module neither executes controls nor authenticates a receipt: a matching
fingerprint cannot establish quiescence, scoring, quota enforcement or cleanup.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

SOURCE_SCHEMA = "harnessgrad.gaia-source.v1"
RUNTIME_SCHEMA = "harnessgrad.gaia-runtime.v1"
ATTESTATION_SCHEMA = "harnessgrad.gaia-compatibility.v1"

# These two files are exclusively this offline review's output and tests.
# No directory-wide docs/tests exemption: such files can be runtime inputs.
REVIEW_ONLY = frozenset({
    "reports/GAIA_GATE.md", "tests/quality/test_gaia_snapshot_attestation.py",
})
REQUIRED_SOURCE = frozenset({
    "data/datasets/gaia.py", "data/datasets/gaia/control.py", "data/scaffolds/command_control.py",
    "data/datasets/gaia/verify/check.py", "data/datasets/gaia/Dockerfile",
    "eval/container.py", "eval/container_state.py", "eval/runner.py",
    "eval/verifier.py",
})
RUNTIME_HASHES = frozenset({
    "pool_sha256", "split_sha256", "selection_sha256", "scorer_sha256",
    "harness_sha256", "dependencies_sha256", "web_policy_sha256",
    "export_contract_sha256", "quota_policy_sha256",
})
RUNTIME_KEYS = RUNTIME_HASHES | {
    "schema", "dataset_revision", "split", "image_id", "verifier_image_id",
    "container_plan", "snapshot_max_bytes", "snapshot_reserve_bytes", "limits",
}


class CompatibilityError(ValueError):
    pass


def _matches(value, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")).hexdigest()


def _source(source: dict) -> None:
    if (not isinstance(source, dict)
            or set(source) != {"schema", "commit", "tree", "files"}
            or source["schema"] != SOURCE_SCHEMA
            or not _matches(source["commit"], r"[a-f0-9]{40}")
            or not _matches(source["tree"], r"[a-f0-9]{40}")
            or not isinstance(source["files"], dict)
            or not REQUIRED_SOURCE <= source["files"].keys()):
        raise CompatibilityError("incomplete committed source identity")
    for path, entry in source["files"].items():
        if (not isinstance(path, str) or not path or "\\" in path
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or PurePosixPath(path).is_absolute()
                or str(PurePosixPath(path)) != path
                or ".." in PurePosixPath(path).parts
                or not isinstance(entry, dict) or set(entry) != {"mode", "oid"}
                or entry["mode"] not in {"100644", "100755", "120000"}
                or not _matches(entry["oid"], r"[a-f0-9]{40}")):
            raise CompatibilityError("invalid source tree entry")
    if any(source["files"][p]["mode"] != "100644" for p in REVIEW_ONLY
           if p in source["files"]):
        raise CompatibilityError("review-only files must be regular nonexecutable files")


def _runtime(binding: dict) -> None:
    if (not isinstance(binding, dict) or set(binding) != RUNTIME_KEYS
            or binding["schema"] != RUNTIME_SCHEMA or binding["split"] != "validation"
            or not _matches(binding["dataset_revision"], r"[a-f0-9]{40}")
            or any(not _matches(binding[key], r"[a-f0-9]{64}") for key in RUNTIME_HASHES)
            or any(not _matches(binding[key], r"sha256:[a-f0-9]{64}")
                   for key in ("image_id", "verifier_image_id"))
            or any(type(binding[key]) is not int or binding[key] <= 0
                   for key in ("container_plan", "snapshot_max_bytes", "snapshot_reserve_bytes"))
            or not isinstance(binding["limits"], dict)
            or set(binding["limits"]) != {"cpus", "memory_mb", "pids"}
            or any(type(value) is not int or value <= 0 for value in binding["limits"].values())):
        raise CompatibilityError("incomplete or unpinned runtime binding")


def collect_source(repo: Path, commit: str, *, require_checkout: bool = False) -> dict:
    """Read one full SHA-1 commit's tree metadata; never read blob contents.

    A candidate must use require_checkout=True. The clean check excludes ignored
    files, so external/ignored runtime inputs still need their own reviewed hashes.
    Historical identities describe commits, not the checkouts used by old runs.
    No receipt provenance is inferred from this collection.
    """
    if not _matches(commit, r"[a-f0-9]{40}"):
        raise CompatibilityError("a full commit hash is required")

    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "--no-replace-objects", "-C", str(repo),
                 "-c", "core.fsmonitor=false", *args],
                check=True, capture_output=True, text=True, errors="replace", timeout=30)
        except (subprocess.SubprocessError, OSError, UnicodeError) as exc:
            raise CompatibilityError("source metadata unavailable") from exc
        return result.stdout

    if require_checkout:
        if git("rev-parse", "HEAD").strip() != commit:
            raise CompatibilityError("candidate checkout does not match commit")
        if git("status", "--porcelain=v1", "--untracked-files=all").strip():
            raise CompatibilityError("candidate checkout is dirty")
    if git("cat-file", "-t", commit).strip() != "commit":
        raise CompatibilityError("source identity must name a commit object")
    tree = git("rev-parse", f"{commit}^{{tree}}").strip()
    files = {}
    for record in git("ls-tree", "-rz", "--full-tree", commit).split("\0"):
        if not record:
            continue
        try:
            metadata, path = record.split("\t", 1)
            mode, kind, oid = metadata.split(" ")
        except ValueError as exc:
            raise CompatibilityError("malformed source metadata") from exc
        if kind != "blob" or path in files:
            raise CompatibilityError("unbound submodule or duplicate source entry")
        files[path] = {"mode": mode, "oid": oid}
    source = {"schema": SOURCE_SCHEMA, "commit": commit, "tree": tree, "files": files}
    _source(source)
    return source


def attest_compatibility(reference: dict, candidate: dict,
                         reference_runtime: dict, candidate_runtime: dict,
                         *, reference_receipt_sha256: str | None = None) -> dict:
    """Compare complete, trusted manifests; produce a review-only attestation.

    Manually supplied dictionaries are trusted inputs, not authenticated Git trees.
    Prefer collect_source() and keep its full output. A receipt digest only names
    evidence for separate review; it never proves the receipt's contents or origin.
    """
    _source(reference)
    _source(candidate)
    _runtime(reference_runtime)
    _runtime(candidate_runtime)
    if (reference_receipt_sha256 is not None
            and not _matches(reference_receipt_sha256, r"[a-f0-9]{64}")):
        raise CompatibilityError("invalid reference receipt digest")
    paths = set(reference["files"]) | set(candidate["files"])
    changed = sorted(p for p in paths
                     if reference["files"].get(p) != candidate["files"].get(p))
    runtime_changes = [p for p in changed if p not in REVIEW_ONLY]
    binding_changes = sorted(k for k in RUNTIME_KEYS
                             if reference_runtime[k] != candidate_runtime[k])
    return {
        "schema": ATTESTATION_SCHEMA,
        "reference_commit": reference["commit"], "candidate_commit": candidate["commit"],
        "reference_source_sha256": _digest(reference),
        "candidate_source_sha256": _digest(candidate),
        "reference_runtime_sha256": _digest(reference_runtime),
        "candidate_runtime_sha256": _digest(candidate_runtime),
        "changed_paths": changed, "runtime_changed_paths": runtime_changes,
        "binding_changed_fields": binding_changes,
        "compatible": not runtime_changes and not binding_changes,
        "reference_receipt_sha256": reference_receipt_sha256,
        "receipt_validation": "not-performed",
        "evidence_reusable": False, "lifecycle_authorized": False,
        "lifecycle_status": "blocked-pending-measured-control-and-cleanup-review",
    }
