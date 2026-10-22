"""Review GAIA Git identities and refusal metadata; never admit execution.

Invoke with ``python3 -m eval.policy.gaia_preflight``. The only external inputs are
inline, strictly validated identity declarations. There are no dataset, receipt,
runtime-file, output-path, backend, task-selection or execution arguments.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.policy import gaia_snapshot_attestation as compatibility

SCHEMA = "harnessgrad.gaia-dry-preflight.v1"
BLOCKED_EXIT = 2
MAX_BINDING_CHARS = 16 * 1024
REMAINING_GATES = (
    "selected-only-provenance", "source-lock-through-sealing",
    "trusted-verifier-runtime", "offline-scorer-dependency-closure",
    "artifact-only-export", "observed-resource-network-and-byte-inode-limits",
    "measured-snapshot-lifecycle", "verified-owned-cleanup",
    "selected-official-oracle-and-noop-controls", "independent-receipt-review",
)


class MetadataError(ValueError):
    pass


class MetadataParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown arguments, including private contents.
        raise MetadataError("invalid metadata-only arguments")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError("duplicate binding key")
        result[key] = value
    return result


def _reject_constant(value):
    raise MetadataError("nonfinite binding value")


def _binding(raw: str) -> dict:
    if len(raw) > MAX_BINDING_CHARS:
        raise MetadataError("oversized binding")
    binding = json.loads(raw, object_pairs_hook=_unique_object,
                         parse_constant=_reject_constant)
    compatibility._runtime(binding)
    return binding


def _runner_refusal() -> dict:
    from eval.run.runner import _refuse_unobserved_policies

    # Fixed projection of GAIA's policy field presence, not a loaded task or an
    # effective runtime observation. None values deliberately assert no limits.
    env = {"kind": "exec", "state": "container", "network": "services",
           "cpus": None, "memory_mb": None,
           "agent_timeout_s": None, "verify_timeout_s": None}
    result = _refuse_unobserved_policies(
        {"task_id": "metadata-only-policy-probe"}, env, {"timeout_s": None})
    invalid = result.get("invalid") if isinstance(result, dict) else None
    receipt = invalid.get("receipt") if isinstance(invalid, dict) else None
    if (not isinstance(receipt, dict)
            or receipt.get("schema") != "harnessgrad.runner-policy-refusal.v1"
            or receipt.get("stage") != "admission"
            or receipt.get("status") != "blocked"
            or receipt.get("reason") != "backend_observation_unavailable"
            or receipt.get("observation") != "unknown"
            or receipt.get("measured") is not False
            or receipt.get("production_authorized") is not False
            or "score" in receipt):
        raise MetadataError("refusal hook did not return the expected refusal")
    return receipt


def _blocked(status: str) -> dict:
    return {"schema": SCHEMA, "scope": "source-and-inline-identity-metadata-only",
            "preflight_status": status, "execution_status": "blocked",
            "measured": False, "production_authorized": False,
            "lifecycle_authorized": False, "evidence_reusable": False,
            "receipt_validation": "not-performed"}


def main(argv: list[str] | None = None) -> int:
    parser = MetadataParser(
        description="GAIA metadata only; reviews exit 2 with execution blocked",
        allow_abbrev=False)
    parser.add_argument("--reference-commit", required=True, help="full SHA-1 commit")
    parser.add_argument("--candidate-commit", required=True,
                        help="full SHA-1 matching this module's clean checkout HEAD")
    parser.add_argument("--reference-runtime", help="inline gaia-runtime.v1 JSON; no file reads")
    parser.add_argument("--candidate-runtime", help="inline gaia-runtime.v1 JSON; no file reads")
    try:
        args = parser.parse_args(argv)
        if (args.reference_runtime is None) != (args.candidate_runtime is None):
            raise MetadataError("both inline bindings are required for comparison")
        bindings = None
        if args.reference_runtime is not None:
            bindings = (_binding(args.reference_runtime), _binding(args.candidate_runtime))
        repo = Path(__file__).resolve().parents[2]
        candidate = compatibility.collect_source(repo, args.candidate_commit,
                                                 require_checkout=True)
        reference = compatibility.collect_source(repo, args.reference_commit)
        changed = sorted(p for p in set(reference["files"]) | set(candidate["files"])
                         if reference["files"].get(p) != candidate["files"].get(p))
        runtime_changed = [p for p in changed if p not in compatibility.REVIEW_ONLY]
        report = _blocked("review-only")
        report.update(
            source_manifests={"reference": reference, "candidate": candidate},
            source_comparison={
                "reference_source_sha256": compatibility._digest(reference),
                "candidate_source_sha256": compatibility._digest(candidate),
                "changed_paths": changed, "runtime_changed_paths": runtime_changed,
                "source_compatible_only": not runtime_changed,
                "checkout_lock": "not-held", "ignored_inputs": "not-inspected"},
            runtime_compatibility=(compatibility.attest_compatibility(
                reference, candidate, *bindings) if bindings is not None else {
                    "status": "not-performed",
                    "reason": "reviewed-inline-runtime-bindings-not-supplied"}),
            runtime_binding_provenance="caller-declared-not-authenticated",
            runner_policy_probe_scope="fixed-GAIA-policy-field-presence-only",
            runner_refusal=_runner_refusal(), remaining_gates=list(REMAINING_GATES))
    except (ValueError, OSError, TypeError, RecursionError):
        report = _blocked("invalid-metadata")
        report["reason"] = "metadata-or-refusal-review-unavailable"
    print(json.dumps(report, sort_keys=True))
    # Even identical identities cannot produce a successful admission exit.
    return BLOCKED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
