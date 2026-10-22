"""Pure, immutable lifecycle receipts for trusted orchestration.

No execution, observation collection, deletion, lease updates or authorization.
Digests bind supplied evidence; they do not authenticate its origin. Backends
must collect facts independently of the actor and persist each returned receipt.
"""
from __future__ import annotations

import copy
import hashlib
import json

from eval.admission import runner_admission as admission

SCHEMA = "harnessgrad.lifecycle-receipt.v1"
ATTESTATION_SCHEMA = "harnessgrad.lifecycle-attestation.v1"
STAGES = ("scope", "admit", "execute", "quiesce", "score", "seal", "cleanup")
MAX_BYTES = 128 * 1024


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _bounded(value):
    _require(len(json.dumps(value, allow_nan=False).encode()) <= MAX_BYTES,
             "lifecycle receipt too large")


def start_lifecycle(binding: dict, scope_sha256: str) -> dict:
    """Freeze scope before admission; scope hash names the pre-execution plan."""
    admission._binding(binding)
    admission._sha(scope_sha256, "invalid_scope_hash")
    return dict(schema=SCHEMA, binding=copy.deepcopy(binding), scope_sha256=scope_sha256,
                state="scope", status="passed", events=[], publication_ready=False,
                production_authorized=False)


def _gate(binding, stage, evidence):
    # Recheck original supplied facts with the shared gate, rather than trusting a
    # free-standing status boolean or an adapter-provided admission receipt.
    if isinstance(evidence, dict) and set(evidence) == {"refusal"}:
        # A persisted refusal can only deny progress. Never persist rejected raw
        # input, which may include task payloads, private logs or credentials.
        receipt = evidence["refusal"]
        keys = {"schema", "stage", "status", "measured", "production_authorized", "binding", "reason"}
        if stage == "cleanup":
            keys.add("lease_action")
        _require(isinstance(receipt, dict) and keys <= set(receipt)
                 and set(receipt) <= keys | {"policy_sha256", "observation_sha256"}
                 and receipt["schema"] == "harnessgrad.runner-admission.v1"
                 and receipt["stage"] == stage and receipt["status"] == "blocked"
                 and receipt["measured"] is False and receipt["production_authorized"] is False
                 and admission._binding(receipt["binding"]) == binding
                 and (stage != "cleanup" or receipt["lease_action"] == "quarantine"),
                 "invalid persisted admission refusal")
        admission._text(receipt["reason"], "invalid_refusal_reason")
        for key in ("policy_sha256", "observation_sha256"):
            if key in receipt:
                admission._sha(receipt[key], "invalid_refusal_hash")
        return receipt
    _require(isinstance(evidence, dict) and set(evidence) == {"policy", "observed"},
             "admission evidence requires policy and observed facts")
    try:
        return getattr(admission, "admit_" + stage)(binding, **evidence)
    except admission.AdmissionError as exc:
        _require(exc.receipt is not None, "missing admission refusal receipt")
        return exc.receipt


def _attestation(binding, stage, evidence):
    _require(isinstance(evidence, dict) and set(evidence) == {
        "schema", "binding", "stage", "status", "receipt_sha256", "facts", "detail"},
        "invalid lifecycle attestation")
    _require(evidence["schema"] == ATTESTATION_SCHEMA and evidence["stage"] == stage,
             "wrong lifecycle attestation stage")
    _require(admission._binding(evidence["binding"]) == binding, "stale lifecycle attestation")
    _require(evidence["status"] in ("passed", "failed", "unknown"), "invalid attestation status")
    admission._text(evidence["detail"], "invalid_attestation_detail")
    _require(isinstance(evidence["facts"], dict), "invalid attestation facts")
    if evidence["status"] != "passed":
        _require(evidence["facts"] == {}, "failed/unknown attestation must not claim passing facts")
        if evidence["receipt_sha256"] is not None:
            admission._sha(evidence["receipt_sha256"], "invalid_attestation_hash")
        return evidence
    admission._sha(evidence["receipt_sha256"], "invalid_attestation_hash")
    facts = evidence["facts"]
    if stage in ("execute", "quiesce"):
        keys = {"exit_code", "oom_killed", "timed_out"}
        if stage == "quiesce":
            keys.add("running")
        _require(set(facts) == keys and type(facts["exit_code"]) is int
                 and facts["exit_code"] == 0 and facts["oom_killed"] is False
                 and facts["timed_out"] is False
                 and (stage != "quiesce" or facts["running"] is False),
                 "successful execution/quiescence unproven")
    elif stage == "score":
        _require(set(facts) == {"outcomes_sha256"}, "invalid scoring attestation")
        admission._sha(facts["outcomes_sha256"], "invalid_outcomes_hash")
    elif stage == "seal":
        _require(set(facts) == {"outcomes_sha256", "snapshot"}, "invalid seal attestation")
        admission._sha(facts["outcomes_sha256"], "invalid_outcomes_hash")
        snapshot = facts["snapshot"]
        _attestation(binding, "snapshot", snapshot)
        _require(snapshot["status"] == "passed", "snapshot completion must pass before seal")
    elif stage == "snapshot":
        # A preflight receipt alone cannot attest completed snapshot/export work.
        _require(set(facts) == {"completed", "artifact_sha256", "exit_code", "oom_killed",
                               "timed_out", "preflight", "runtime"}, "invalid snapshot completion attestation")
        _require(facts["completed"] is True and type(facts["exit_code"]) is int
                 and facts["exit_code"] == 0 and facts["oom_killed"] is False
                 and facts["timed_out"] is False, "snapshot completion unproven")
        admission._sha(facts["artifact_sha256"], "invalid_snapshot_artifact_hash")
        _require(_gate(binding, "snapshot", facts["preflight"])["status"] == "passed",
                 "snapshot preflight must pass")
        _require(_gate(binding, "runtime", facts["runtime"])["status"] == "passed",
                 "snapshot worker runtime admission must pass")
    return evidence


def _advance(receipt, stage, evidence):
    _require(stage in STAGES[1:], "unknown lifecycle stage")
    _require(receipt["state"] != "cleanup", "cleanup is terminal; use a fresh run ID")
    if receipt["status"] != "passed":
        _require(stage == "cleanup", "failed lifecycle may only attempt cleanup")
    else:
        _require(stage == STAGES[STAGES.index(receipt["state"]) + 1],
                 "lifecycle stage skipped or repeated")
    if stage in ("admit", "cleanup"):
        checked = _gate(receipt["binding"], "runtime" if stage == "admit" else stage, evidence)
        status = "passed" if checked["status"] == "passed" else "failed"
        if status == "failed":
            evidence = {"refusal": checked}
    else:
        checked = _attestation(receipt["binding"], stage, evidence)
        status = checked["status"]
    if stage == "seal" and status == "passed":
        scored = receipt["events"][-1]["evidence"]["facts"]["outcomes_sha256"]
        _require(checked["facts"]["outcomes_sha256"] == scored, "sealed outcomes differ from scoring")
    result = copy.deepcopy(receipt)
    result["events"].append(dict(stage=stage, status=status,
                                 evidence=copy.deepcopy(evidence), receipt_sha256=digest(checked)))
    result["state"] = stage
    # Failed/unknown work remains terminal for scoring even after successful cleanup.
    result["status"] = status if receipt["status"] == "passed" else receipt["status"]
    result["publication_ready"] = stage == "cleanup" and result["status"] == "passed"
    _bounded(result)
    return result


def validate_lifecycle_receipt(receipt: dict) -> None:
    """Replay checks without trusting derived states, statuses or event digests."""
    _require(isinstance(receipt, dict) and set(receipt) == {
        "schema", "binding", "scope_sha256", "state", "status", "events",
        "publication_ready", "production_authorized"} and receipt["schema"] == SCHEMA,
        "unsupported lifecycle receipt")
    _bounded(receipt)
    _require(isinstance(receipt["events"], list) and len(receipt["events"]) <= 6,
             "invalid lifecycle event count")
    replay = start_lifecycle(receipt["binding"], receipt["scope_sha256"])
    for event in receipt["events"]:
        _require(isinstance(event, dict) and set(event) == {
            "stage", "status", "evidence", "receipt_sha256"}, "invalid lifecycle event")
        replay = _advance(replay, event["stage"], event["evidence"])
    _require(replay == receipt and type(receipt["publication_ready"]) is bool
             and receipt["production_authorized"] is False, "inconsistent lifecycle receipt")


def transition(receipt: dict, stage: str, evidence: dict) -> dict:
    """Return a new checkpoint. No skipped/repeated phases or post-failure scoring."""
    validate_lifecycle_receipt(receipt)
    return _advance(receipt, stage, evidence)


def require_scope(receipt: dict, *, run_id: str, manifest_sha256: str, scope_sha256: str) -> None:
    """Check even incomplete checkpoints against their frozen publication scope."""
    validate_lifecycle_receipt(receipt)
    _require(receipt["binding"]["run_id"] == run_id
             and receipt["binding"]["manifest_sha256"] == manifest_sha256
             and receipt["scope_sha256"] == scope_sha256, "publication scope/binding changed")


def require_publication(receipt: dict, *, run_id: str, manifest_sha256: str,
                        scope_sha256: str, outcomes_sha256: str,
                        cleanup_sha256: str, snapshot_sha256: str) -> None:
    """Permit structural publication only for this exact scope and sealed result."""
    require_scope(receipt, run_id=run_id, manifest_sha256=manifest_sha256, scope_sha256=scope_sha256)
    _require(receipt["publication_ready"], "cleanup and snapshot lifecycle must pass before publication")
    seal, cleanup = receipt["events"][-2:]
    facts = seal["evidence"]["facts"]
    _require(facts["outcomes_sha256"] == outcomes_sha256, "publication outcomes changed after seal")
    _require(cleanup["receipt_sha256"] == cleanup_sha256
             and digest(facts["snapshot"]) == snapshot_sha256,
             "publication cleanup and snapshot attestations differ")
