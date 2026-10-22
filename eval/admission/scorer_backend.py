"""Adapter-neutral, evidence-only scorer backend preflight.

Trusted orchestration first quiesces the actor and calls
``scorer_admission.export_state`` into an exclusive platform-owned destination.
It then supplies that receipt, the quiesced lifecycle checkpoint, a pristine
runtime inspection in artifact-admission's existing shape, and independently
observed resource enforcement. This module only rechecks those inputs and local
projection bytes. It neither invokes backend callbacks nor advances lifecycle.

Backend callbacks/ownership still required outside this interface:
* reserve a fresh run/trial and persist independently trusted scope/policy pins;
* collect actor exit/quiescence facts and keep the source stopped through export;
* own the export parent, closure and pinned image, keeping both trees immutable;
* record the export's association with this exact trial/quiescence checkpoint
  (the existing export receipt itself binds only run, task and contract);
* provision a stopped scorer runtime and collect fresh inspection and enforcement
  facts from that same instance, independently of the actor/adapter;
* durably persist checkpoints/refusals, enforce deadlines and resource limits,
  and inventory/clean up all owned resources, quarantining uncertain cleanup.

Digests bind supplied facts, not observer authenticity, freshness or ownership.
Official scorer execution, result validation, snapshot completion and cleanup are
separate obligations. No callback implementation or production runner is wired in.
"""
from __future__ import annotations

import copy
from pathlib import Path

from eval.admission import lifecycle_receipt as lifecycle
from eval.admission import runner_admission as resources
from eval.admission import scorer_admission as scorer

__all__ = ("ScorerBackendError", "check_backend")
SCHEMA = "harnessgrad.scorer-backend-receipt.v1"


class ScorerBackendError(scorer.AdmissionError):
    """A sanitized blocked/unknown preflight; never a measured zero score."""

    def __init__(self, code: str, receipt: dict):
        super().__init__(code)
        self.code = code
        self.receipt = receipt


def check_backend(*, binding: dict, scope_sha256: str,
                  lifecycle_receipt: dict | None,
                  bundle_raw: bytes, bundle_sha256: str,
                  contract_raw: bytes, contract_sha256: str,
                  closure_root: Path, artifact_root: Path,
                  export_receipt: dict | None, info: dict | None,
                  runtime_policy: dict | None, runtime_policy_sha256: str,
                  runtime_observed: dict | None) -> dict:
    """Compose existing checks, with no execution, export writes or authorization.

    Expected binding, scope, bundle, task contract and resource-policy pins come
    from trusted caller storage, never from actor state or an incoming receipt.
    Resource observations use runner_admission's binding/schema; filesystem
    policy must exactly match the task contract's writable quota surfaces.
    ``None`` denotes unavailable evidence. Contradictions are blocked; missing or
    incomplete scorer observations retain scorer_admission's unknown status.
    Neither can become a score.

    A pass covers supplied-evidence consistency only. Every call replays lifecycle
    and rechecks local bytes; earlier preflight receipts are not accepted as input.
    The original checkpoint stays at quiesce and still needs score/seal/cleanup.
    """
    receipt = dict(schema=SCHEMA, stage="lifecycle", status="unknown", measured=False,
                   production_authorized=False, execution_authorized=False,
                   publication_ready=False)
    try:
        # Validate independent expectations before inspecting the supplied receipt.
        lifecycle.start_lifecycle(binding, scope_sha256)
        if lifecycle_receipt is None:
            raise scorer.EvidenceUnavailable("lifecycle evidence missing")
        lifecycle.require_scope(lifecycle_receipt, run_id=binding["run_id"],
                                manifest_sha256=binding["manifest_sha256"],
                                scope_sha256=scope_sha256)
        if (lifecycle_receipt["binding"] != binding
                or lifecycle_receipt["state"] != "quiesce"
                or lifecycle_receipt["status"] != "passed"):
            raise scorer.AdmissionError("matching successful quiescence required")

        receipt["stage"] = "resources"
        if runtime_policy is None:
            raise scorer.EvidenceUnavailable("runtime enforcement evidence missing")
        # Check supplied policy pins before classifying unavailable observations.
        if lifecycle.digest(runtime_policy) != runtime_policy_sha256:
            raise scorer.AdmissionError("runtime policy pin mismatch")
        if runtime_observed is None:
            raise scorer.EvidenceUnavailable("runtime enforcement evidence missing")
        resource_check = resources.admit_runtime(binding, runtime_policy, runtime_observed)
        contract = scorer.load_contract(contract_raw, contract_sha256)
        if runtime_policy["filesystems"] != contract["quota"]:
            raise scorer.AdmissionError("runtime filesystem policy differs from artifact contract")

        receipt["stage"] = "scorer"
        scorer_check = scorer.admit_runtime(
            bundle_raw=bundle_raw, bundle_sha256=bundle_sha256,
            contract_raw=contract_raw, contract_sha256=contract_sha256,
            closure_root=closure_root, artifact_root=artifact_root,
            export_receipt=export_receipt, info=info, run_id=binding["run_id"])
        receipt.update(stage="preflight", status="passed", binding=copy.deepcopy(binding),
                       scope_sha256=scope_sha256,
                       evidence=dict(lifecycle_sha256=lifecycle.digest(lifecycle_receipt),
                                     export_sha256=lifecycle.digest(export_receipt),
                                     scorer=scorer_check, resources=resource_check))
        return receipt
    except scorer.ScorerAdmissionError as exc:
        status = exc.receipt["status"]
    except scorer.EvidenceUnavailable:
        status = "unknown"
    except (scorer.AdmissionError, resources.AdmissionError, ValueError, TypeError,
            KeyError, AttributeError, RecursionError):
        status = "blocked"
    suffix = "evidence_unavailable" if status == "unknown" else "contract_refused"
    code = f"{receipt['stage']}_{suffix}"
    receipt.update(status=status, reason=code)
    raise ScorerBackendError(code, receipt) from None
