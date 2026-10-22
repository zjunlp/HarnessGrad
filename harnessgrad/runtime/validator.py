"""Structural and semantic validation for eight-stage event streams."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - production installs jsonschema
    Draft202012Validator = None

SCHEMA = Path(__file__).with_name("eight_stage_protocol.schema.json")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
STAGES = {"select_context", "execute", "organize_feedback", "propose_changes", "build_validate",
          "evaluate_candidates", "select_deploy", "stop_continue"}
PAYLOAD_STAGES = {
    "context_selection": {"select_context"}, "execution": {"execute"},
    "feedback": {"organize_feedback"}, "proposal": {"propose_changes"},
    "validation": {"build_validate"}, "study_evaluation": {"evaluate_candidates"},
    "heldout_evaluation": {"evaluate_candidates"},
    "selection_result": {"select_deploy"}, "deployment_fact": {"select_deploy"},
    "continuation": {"stop_continue"},
}
TRUSTED_FACT_ROLES = {"framework", "evaluator", "deployer", "recorder", "delegate"}
ARTIFACT_AUTHORITY = {
    "validation": {"framework", "recorder"},
    "deployment": {"framework", "deployer", "recorder"},
    "study_score": {"framework", "evaluator", "recorder", "delegate"},
    "heldout_score": TRUSTED_FACT_ROLES,
}
PAYLOAD_KEYS = {
    "context_selection": {"payload_kind", "task_set_sha", "parent_refs", "selection_basis"},
    "execution": {"payload_kind", "execution_mode", "replay_kind", "source_trace_ids", "new_execution", "training_scope"},
    "feedback": {"payload_kind", "source_artifact_ids", "consumer_scope", "feedback_consumers", "consumer_role"},
    "proposal": {"payload_kind", "candidate_ids", "artifact_kind", "hypothesis"},
    "validation": {"payload_kind", "candidate_ids", "valid", "checks"},
    "study_evaluation": {"payload_kind", "score_kind", "candidate_ids", "scorer_revision", "evaluation_completeness"},
    "heldout_evaluation": {"payload_kind", "score_kind", "candidate_ids", "scorer_revision", "evaluation_completeness"},
    "selection_result": {"payload_kind", "selected_candidate_ids", "basis", "tentative", "later_eval_ref"},
    "deployment_fact": {"payload_kind", "deployment_id", "deployed_candidate_ids", "status", "from_snapshot", "to_snapshot", "rollback_ref"},
    "continuation": {"payload_kind", "action", "next_scope"}, "archive": {"payload_kind", "member_ids", "retention_rule"},
    "reconciliation": {"payload_kind", "reconciles_event_ids"}, "empty_result": {"payload_kind", "reason"},
}


class ProtocolError(ValueError):
    pass


def _id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ProtocolError(f"invalid_{label}")


def _hash(value: Any, label: str) -> None:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ProtocolError(f"invalid_{label}_hash")


def _finite_number(value: Any, label: str) -> None:
    # bool is an int subclass, but JSON Schema's number type excludes it.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"invalid_{label}")


def _bool(value: Any, label: str) -> None:
    if not isinstance(value, bool):
        raise ProtocolError(f"invalid_{label}")


def _id_array(value: Any, label: str, *, unique: bool = False) -> None:
    if not isinstance(value, list):
        raise ProtocolError(f"invalid_{label}")
    for item in value:
        _id(item, label[:-1] if label.endswith("s") else label)
    if unique and len(set(value)) != len(value):
        raise ProtocolError(f"duplicate_{label}")


def _principal(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) - {"role", "id", "version"}:
        raise ProtocolError(f"invalid_{label}")
    if not isinstance(value.get("role"), str) or not isinstance(value.get("id"), str):
        raise ProtocolError(f"invalid_{label}")
    _id(value["id"], f"{label}_id")


def _structural_validate(event: Any) -> None:
    """Mandatory stdlib structural gate used even when jsonschema is absent."""
    if not isinstance(event, dict): raise ProtocolError("event_must_be_object")
    for key in ("protocol_version", "run_id", "event_id", "event_kind"):
        if key not in event: raise ProtocolError(f"missing_{key}")
    if event["protocol_version"] != "1.0": raise ProtocolError("unsupported_protocol_version")
    _id(event["run_id"], "run")
    _id(event["event_id"], "event")
    if event["event_kind"] == "protocol_bootstrap":
        if set(event) - {"protocol_version", "run_id", "event_id", "event_kind", "producer", "bootstrap"}:
            raise ProtocolError("bootstrap_additional_property")
        _principal(event.get("producer"), "producer")
        if event["producer"].get("role") not in {"framework", "recorder"}:
            raise ProtocolError("bootstrap_authority")
        boot = event.get("bootstrap")
        if not isinstance(boot, dict): raise ProtocolError("missing_bootstrap")
        for key in ("profile_opt_in", "stage_modes", "task_plan", "evidence_mode", "budget"):
            if key not in boot: raise ProtocolError(f"missing_bootstrap_{key}")
        if not isinstance(boot["stage_modes"], dict) or not isinstance(boot["task_plan"], dict):
            raise ProtocolError("invalid_bootstrap_shape")
        if not isinstance(boot["profile_opt_in"], str) or not boot["profile_opt_in"]:
            raise ProtocolError("invalid_profile_opt_in")
        for stage_id, mode in boot["stage_modes"].items():
            if stage_id not in STAGES or mode not in {"default", "custom", "delegated", "skip"}:
                raise ProtocolError("invalid_stage_modes")
        _hash(boot["task_plan"].get("task_set_sha"), "task_set")
        if set(boot["task_plan"]) - {"task_set_sha", "split_visibility"}:
            raise ProtocolError("invalid_task_plan")
        if "split_visibility" in boot["task_plan"] and boot["task_plan"]["split_visibility"] not in {
            "adaptation_visible", "selection_only", "report_only", "heldout_private"}:
            raise ProtocolError("invalid_split_visibility")
        if boot["evidence_mode"] not in {"trusted_events", "replay_only", "declared_traces"}:
            raise ProtocolError("invalid_evidence_mode")
        budget = boot["budget"]
        if (not isinstance(budget, dict) or set(budget) - {"unit", "limit"}
                or budget.get("unit") not in {"tokens", "usd", "wall_clock", "api_calls", "generations", "training_steps"}):
            raise ProtocolError("invalid_budget")
        try:
            _finite_number(budget.get("limit"), "budget_limit")
        except ProtocolError:
            raise ProtocolError("invalid_budget_limit")
        if budget["limit"] <= 0:
            raise ProtocolError("invalid_budget_limit")
        delegates = boot.get("delegates", [])
        if not isinstance(delegates, list):
            raise ProtocolError("invalid_delegates")
        seen_delegates: set[str] = set()
        for delegate in delegates:
            if (not isinstance(delegate, dict)
                    or set(delegate) - {"delegate_id", "capabilities", "timeout_ms"}
                    or set({"delegate_id", "capabilities", "timeout_ms"}) - set(delegate)):
                raise ProtocolError("invalid_delegate")
            _id(delegate["delegate_id"], "delegate")
            if delegate["delegate_id"] in seen_delegates:
                raise ProtocolError("duplicate_delegate")
            seen_delegates.add(delegate["delegate_id"])
            if (not isinstance(delegate["capabilities"], list)
                    or any(not isinstance(cap, str) for cap in delegate["capabilities"])):
                raise ProtocolError("invalid_delegate_capabilities")
            if len(set(delegate["capabilities"])) != len(delegate["capabilities"]):
                raise ProtocolError("invalid_delegate_capabilities")
            if (isinstance(delegate["timeout_ms"], bool) or not isinstance(delegate["timeout_ms"], int)
                    or delegate["timeout_ms"] < 1):
                raise ProtocolError("invalid_delegate_timeout")
        return
    if event["event_kind"] not in {"stage_started", "stage_completed", "stage_failed", "artifact_published", "reconciliation"}:
        raise ProtocolError("invalid_event_kind")
    allowed_top = {"protocol_version", "run_id", "event_id", "event_kind", "scope", "stage", "lifecycle", "evidence", "owner", "producer", "implementation", "artifacts", "input_artifacts", "output_artifacts", "result_kind", "causal_parent_events", "attempt_id", "attempt", "idempotency_key", "parallel_group", "replay_of", "concurrency_group", "started_at", "finished_at", "reason", "preconditions", "invariants", "partial_results", "reconciles_event_ids", "failure", "claims", "payload"}
    if set(event) - allowed_top: raise ProtocolError("event_additional_property")
    required = ("scope", "stage", "lifecycle", "evidence", "owner", "producer", "implementation",
                "artifacts", "input_artifacts", "output_artifacts", "result_kind", "attempt_id",
                "attempt", "idempotency_key", "started_at", "finished_at", "payload")
    for key in required:
        if key not in event: raise ProtocolError(f"missing_{key}")
    scope = event["scope"]
    if not isinstance(scope, dict): raise ProtocolError("invalid_scope")
    if set(scope) - {"run_id", "session_id", "iteration_id", "branch_id", "candidate_id", "candidate_ids", "task_id", "trial_id", "parent_scope", "stream_id", "batch_id", "deployment_epoch", "scope_id"}:
        raise ProtocolError("scope_additional_property")
    for key in ("run_id", "session_id", "iteration_id", "branch_id"):
        _id(scope.get(key), f"scope_{key}")
    if scope["run_id"] != event["run_id"]: raise ProtocolError("scope_run_id_mismatch")
    for key in ("candidate_id", "task_id", "trial_id", "parent_scope", "stream_id", "batch_id", "deployment_epoch", "scope_id"):
        if key in scope: _id(scope[key], f"scope_{key}")
    if "candidate_ids" in scope:
        _id_array(scope["candidate_ids"], "scope_candidate_ids", unique=True)
    stage = event["stage"]
    if not isinstance(stage, dict) or set(stage) - {"id", "invocation_id", "mode", "repeat_index"}:
        raise ProtocolError("invalid_stage")
    if stage.get("id") not in STAGES or stage.get("mode") not in {"default", "custom", "delegated", "skip"}:
        raise ProtocolError("invalid_stage")
    _id(stage.get("invocation_id"), "invocation")
    if "repeat_index" in stage and (isinstance(stage["repeat_index"], bool) or not isinstance(stage["repeat_index"], int) or stage["repeat_index"] < 0):
        raise ProtocolError("invalid_repeat_index")
    if event["lifecycle"] not in {"running", "completed", "failed"} or event["evidence"] not in {"measured", "unobserved"}:
        raise ProtocolError("invalid_lifecycle_or_evidence")
    _principal(event["owner"], "owner"); _principal(event["producer"], "producer")
    roles = {"framework", "task_agent", "meta_agent", "method", "delegate", "evaluator", "deployer", "recorder"}
    if event["owner"]["role"] not in roles or event["producer"]["role"] not in roles:
        raise ProtocolError("invalid_principal_role")
    impl = event["implementation"]
    if not isinstance(impl, dict) or set(impl) - {"module", "compliance", "fidelity", "delegate_id", "capabilities", "timeout_ms", "request_artifact_id", "response_artifact_id", "independent_measurement_refs"}:
        raise ProtocolError("invalid_implementation")
    for key in ("compliance", "fidelity"):
        if impl.get(key) not in ({"declared", "verified", "unverified", "not_applicable"} if key == "compliance" else {"measured", "claimed", "unknown", "not_applicable"}):
            raise ProtocolError(f"invalid_implementation_{key}")
    if "module" in impl and not isinstance(impl["module"], str): raise ProtocolError("invalid_implementation_module")
    if "delegate_id" in impl: _id(impl["delegate_id"], "delegate")
    if "request_artifact_id" in impl: _id(impl["request_artifact_id"], "request_artifact")
    if "response_artifact_id" in impl: _id(impl["response_artifact_id"], "response_artifact")
    if "capabilities" in impl:
        if (not isinstance(impl["capabilities"], list)
                or any(not isinstance(cap, str) for cap in impl["capabilities"])):
            raise ProtocolError("invalid_implementation_capabilities")
        if len(set(impl["capabilities"])) != len(impl["capabilities"]):
            raise ProtocolError("invalid_implementation_capabilities")
    if "timeout_ms" in impl and (isinstance(impl["timeout_ms"], bool)
                                  or not isinstance(impl["timeout_ms"], int) or impl["timeout_ms"] < 1):
        raise ProtocolError("invalid_implementation_timeout")
    if "independent_measurement_refs" in impl:
        _id_array(impl["independent_measurement_refs"], "independent_measurement_refs", unique=True)
    for key in ("artifacts", "input_artifacts", "output_artifacts"):
        if not isinstance(event[key], list): raise ProtocolError(f"{key}_must_be_array")
    if event["result_kind"] not in {"artifact", "empty", "not_observed"}: raise ProtocolError("invalid_result_kind")
    _id(event["attempt_id"], "attempt"); _id(event["idempotency_key"], "idempotency")
    if isinstance(event["attempt"], bool) or not isinstance(event["attempt"], int) or event["attempt"] < 1: raise ProtocolError("invalid_attempt")
    if not isinstance(event["started_at"], str) or not isinstance(event["finished_at"], str): raise ProtocolError("invalid_timestamp")
    if event.get("failure") is not None:
        failure = event["failure"]
        if not isinstance(failure, dict) or set(failure) - {"class", "subject_or_infrastructure", "detail"}:
            raise ProtocolError("invalid_failure")
        if failure.get("class") not in {"invalid_input", "invalid_patch", "subject_timeout", "subject_error", "budget_exhausted", "delegate_error", "evaluator_error", "recorder_error", "infrastructure_unavailable", "cancelled"}:
            raise ProtocolError("invalid_failure_class")
        if failure.get("subject_or_infrastructure") not in {"subject", "infrastructure"}:
            raise ProtocolError("invalid_failure_authority")
    payload = event["payload"]
    if not isinstance(payload, dict) or payload.get("payload_kind") not in PAYLOAD_KEYS:
        raise ProtocolError("invalid_payload")
    if set(payload) - PAYLOAD_KEYS[payload["payload_kind"]]: raise ProtocolError("untyped_payload")
    required_payload = {
        "context_selection": ("task_set_sha", "parent_refs", "selection_basis"),
        "execution": ("execution_mode", "new_execution"), "feedback": ("source_artifact_ids", "consumer_scope"),
        "proposal": ("candidate_ids", "artifact_kind", "hypothesis"), "validation": ("candidate_ids", "valid", "checks"),
        "study_evaluation": ("score_kind", "candidate_ids", "scorer_revision", "evaluation_completeness"),
        "heldout_evaluation": ("score_kind", "candidate_ids", "scorer_revision", "evaluation_completeness"),
        "selection_result": ("selected_candidate_ids", "basis", "tentative"),
        "deployment_fact": ("deployment_id", "deployed_candidate_ids", "status", "from_snapshot", "to_snapshot"),
        "continuation": ("action",), "archive": ("member_ids", "retention_rule"),
        "reconciliation": ("reconciles_event_ids",), "empty_result": ("reason",),
    }
    for key in required_payload[payload["payload_kind"]]:
        if key not in payload: raise ProtocolError(f"missing_payload_{key}")
    array_payload_keys = {
        "parent_refs", "source_trace_ids", "source_artifact_ids", "feedback_consumers",
        "candidate_ids", "selected_candidate_ids", "basis", "deployed_candidate_ids",
        "member_ids", "reconciles_event_ids",
    }
    unique_payload_keys = {"source_trace_ids", "source_artifact_ids", "feedback_consumers",
                           "candidate_ids", "selected_candidate_ids", "basis", "deployed_candidate_ids",
                           "member_ids", "reconciles_event_ids"}
    for key in array_payload_keys & set(payload):
        if not isinstance(payload[key], list):
            raise ProtocolError(f"invalid_payload_{key}")
        if key in unique_payload_keys and any(not isinstance(item, str) for item in payload[key]):
            raise ProtocolError(f"invalid_payload_{key}")
        if key in unique_payload_keys and len(set(payload[key])) != len(payload[key]):
            raise ProtocolError(f"duplicate_payload_{key}")
    for key in ("source_trace_ids", "source_artifact_ids", "candidate_ids", "selected_candidate_ids",
                "basis", "deployed_candidate_ids", "member_ids", "reconciles_event_ids"):
        if key in payload:
            for value in payload[key]:
                _id(value, f"payload_{key[:-4] if key.endswith('_ids') else key}")
    if "new_execution" in payload: _bool(payload["new_execution"], "payload_new_execution")
    if "tentative" in payload: _bool(payload["tentative"], "payload_tentative")
    if "valid" in payload: _bool(payload["valid"], "payload_valid")
    for key in ("selection_basis", "hypothesis", "scorer_revision", "consumer_scope", "consumer_role",
                "training_scope", "retention_rule", "reason", "action", "next_scope"):
        if key in payload and not isinstance(payload[key], str):
            raise ProtocolError(f"invalid_payload_{key}")
    if payload["payload_kind"] in {"context_selection"} : _hash(payload["task_set_sha"], "task_set")
    if payload["payload_kind"] == "execution" and payload["execution_mode"] not in {"online", "replay"}: raise ProtocolError("invalid_execution_mode")
    if payload["payload_kind"] in {"study_evaluation", "heldout_evaluation"} and payload["score_kind"] not in {"study", "heldout"}: raise ProtocolError("invalid_score_kind")
    if payload["payload_kind"] == "study_evaluation" and payload["evaluation_completeness"] not in {"partial", "staged", "full"}:
        raise ProtocolError("invalid_study_completeness")
    if payload["payload_kind"] == "heldout_evaluation" and payload["evaluation_completeness"] != "heldout":
        raise ProtocolError("invalid_heldout_completeness")
    if payload["payload_kind"] == "execution" and payload.get("replay_kind") not in {None, "stored_counterfactual", "executed_smoke", "executed_benchmark"}:
        raise ProtocolError("invalid_replay_kind")
    if payload["payload_kind"] == "proposal" and payload["artifact_kind"] not in {"patch", "full_file", "full-file", "file", "tree", "config", "prompt", "skill", "memory", "policy", "checkpoint", "parameter_update", "none"}:
        raise ProtocolError("invalid_proposal_artifact_kind")
    if payload["payload_kind"] == "validation":
        if not isinstance(payload["checks"], list) or any(not isinstance(check, str) for check in payload["checks"]):
            raise ProtocolError("invalid_payload_checks")
    if payload["payload_kind"] in {"study_evaluation", "heldout_evaluation"}:
        if not isinstance(payload["scorer_revision"], str):
            raise ProtocolError("invalid_payload_scorer_revision")
    if payload["payload_kind"] in {"feedback", "selection_result", "study_evaluation", "heldout_evaluation"}:
        for key in ("source_artifact_ids", "basis", "candidate_ids", "selected_candidate_ids"):
            if key in payload:
                if not isinstance(payload[key], list): raise ProtocolError(f"invalid_payload_{key}")
                for value in payload[key]: _id(value, f"payload_{key[:-4] if key.endswith('_ids') else key}")
    if payload["payload_kind"] == "deployment_fact":
        if payload["status"] not in {"deployed", "none", "rolled_back", "failed"}:
            raise ProtocolError("invalid_deployment_status")
        for key in ("deployed_candidate_ids",):
            if not isinstance(payload[key], list): raise ProtocolError(f"invalid_payload_{key}")
            for value in payload[key]: _id(value, "deployment_candidate")




def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def schema_errors(event: dict[str, Any]) -> list[Any]:
    if Draft202012Validator is None:
        raise ProtocolError("jsonschema_unavailable_structural_only")
    schema = _schema(); Draft202012Validator.check_schema(schema)
    return sorted(Draft202012Validator(schema).iter_errors(event), key=lambda e: list(e.path))


def validate_event(event: dict[str, Any]) -> bool:
    return validate_events([event])


def _artifact_index(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in events:
        for a in e.get("artifacts", []) + e.get("input_artifacts", []) + e.get("output_artifacts", []):
            _structural_artifact(a)
            aid = a["artifact_id"]
            if aid in out and out[aid] != a:
                raise ProtocolError(f"artifact_conflict:{aid}")
            out[aid] = a
    return out


def _structural_artifact(a: Any) -> None:
    if not isinstance(a, dict): raise ProtocolError("artifact_must_be_object")
    allowed = {"artifact_id", "kind", "sha256", "uri", "visibility", "producer", "parents", "lineage", "root", "score_kind", "split_visibility", "scorer_revision", "content_kind", "scope"}
    if set(a) - allowed: raise ProtocolError("artifact_additional_property")
    for key in ("artifact_id", "kind", "sha256", "visibility", "producer"):
        if key not in a: raise ProtocolError(f"artifact_missing_{key}")
    _id(a["artifact_id"], "artifact"); _hash(a["sha256"], "artifact")
    _principal(a["producer"], "artifact_producer")
    if a["visibility"] not in {"method", "framework", "heldout_private", "public_record"}:
        raise ProtocolError("invalid_artifact_visibility")
    kinds = {"task_set", "parent_state", "experience", "feedback", "proposal", "candidate", "validation", "study_score", "heldout_score", "selection", "deployment", "stop_decision", "archive", "trace", "claim", "legacy_response", "post_run_analysis", "checkpoint", "tree_snapshot", "prompt", "skill", "memory", "policy", "parameter_update", "patch", "full_file", "full-file", "file", "tree", "config"}
    if a["kind"] not in kinds: raise ProtocolError("invalid_artifact_kind")
    if "uri" in a and not isinstance(a["uri"], str): raise ProtocolError("invalid_artifact_uri")
    if "content_kind" in a and a["content_kind"] not in {"patch", "full_file", "full-file", "file", "tree", "config", "prompt", "skill", "memory", "policy", "checkpoint", "parameter_update", "none"}:
        raise ProtocolError("invalid_artifact_content_kind")
    if "score_kind" in a and a["score_kind"] not in {"study", "heldout"}: raise ProtocolError("invalid_artifact_score_kind")
    if "split_visibility" in a and a["split_visibility"] not in {"adaptation_visible", "selection_only", "report_only", "heldout_private"}:
        raise ProtocolError("invalid_artifact_split_visibility")
    if "scorer_revision" in a and not isinstance(a["scorer_revision"], str): raise ProtocolError("invalid_artifact_scorer_revision")
    for key in ("parents",):
        if key in a:
            if not isinstance(a[key], list): raise ProtocolError("invalid_artifact_parents")
            seen: set[str] = set()
            for value in a[key]:
                _id(value, "artifact_parent")
                if value in seen: raise ProtocolError("invalid_artifact_parents")
                seen.add(value)
    if "lineage" in a:
        if not isinstance(a["lineage"], list): raise ProtocolError("invalid_artifact_lineage")
        for edge in a["lineage"]:
            if not isinstance(edge, dict) or not isinstance(edge.get("role"), str): raise ProtocolError("invalid_artifact_lineage")
            if set(edge) - {"parent_id", "role"} or edge["role"] not in {"root", "base", "donor", "peer", "crossover", "evidence"}:
                raise ProtocolError("invalid_artifact_lineage")
            if edge["role"] != "root": _id(edge.get("parent_id"), "lineage_parent")
            elif set(edge) != {"role"}: raise ProtocolError("invalid_artifact_lineage")
    if "root" in a and not isinstance(a["root"], bool): raise ProtocolError("invalid_artifact_root")
    if "scope" in a:
        scope = a["scope"]
        if not isinstance(scope, dict): raise ProtocolError("invalid_artifact_scope")
        if set(scope) - {"run_id", "session_id", "iteration_id", "branch_id", "candidate_id", "candidate_ids", "task_id", "trial_id", "parent_scope", "stream_id", "batch_id", "deployment_epoch", "scope_id"}:
            raise ProtocolError("invalid_artifact_scope")
        for key in ("run_id", "session_id", "iteration_id", "branch_id"):
            _id(scope.get(key), f"artifact_scope_{key}")
        for key in ("candidate_id", "task_id", "trial_id", "parent_scope", "stream_id", "batch_id", "deployment_epoch", "scope_id"):
            if key in scope: _id(scope[key], f"artifact_scope_{key}")
        if "candidate_ids" in scope: _id_array(scope["candidate_ids"], "artifact_scope_candidate_ids", unique=True)


def _ancestors(aid: str, arts: dict[str, dict[str, Any]], seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if aid in seen: return set()
    seen.add(aid)
    a = arts.get(aid)
    if not a: return set()
    parents = set(a.get("parents", []))
    parents |= {x.get("parent_id") for x in a.get("lineage", []) if x.get("parent_id")}
    out = set(parents)
    for parent in parents: out |= _ancestors(parent, arts, seen)
    return out


def _check_artifact_cycles(arts: dict[str, dict[str, Any]]) -> None:
    visiting: set[str] = set(); done: set[str] = set()
    def visit(aid: str) -> None:
        if aid in visiting: raise ProtocolError(f"artifact_cycle:{aid}")
        if aid in done: return
        visiting.add(aid)
        a = arts.get(aid, {})
        refs = set(a.get("parents", [])) | {x["parent_id"] for x in a.get("lineage", []) if "parent_id" in x}
        for parent in refs: visit(parent)
        visiting.remove(aid); done.add(aid)
    for aid in arts: visit(aid)


def _require_artifact_refs(ref_ids: Iterable[str], *, arts: dict[str, dict[str, Any]],
                           listed: Iterable[str], label: str) -> None:
    refs = set(ref_ids)
    if not refs <= set(arts):
        raise ProtocolError(f"{label}_artifact_unresolved:{sorted(refs - set(arts))[0]}")
    listed_ids = set(listed)
    if not refs <= listed_ids:
        raise ProtocolError(f"{label}_artifact_not_declared_input:{sorted(refs - listed_ids)[0]}")


def _delegate_authorized(boot: dict[str, Any], principal: dict[str, Any], capability: str) -> bool:
    if principal.get("role") != "delegate":
        return True
    return any(d.get("delegate_id") == principal.get("id") and capability in d.get("capabilities", [])
               for d in boot.get("bootstrap", {}).get("delegates", []))


def _check_parent_refs(payload: dict[str, Any]) -> None:
    refs = payload.get("parent_refs")
    if refs is None: return
    if not isinstance(refs, list): raise ProtocolError("parent_refs_not_array")
    ids: set[str] = set(); roles: set[str] = set(); root_count = 0
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("role"), str):
            raise ProtocolError("typed_parent_ref_required")
        role = ref["role"]
        if role == "root":
            if set(ref) != {"role"}: raise ProtocolError("root_parent_ref_has_parent_id")
            root_count += 1
            continue
        if role not in {"base", "donor", "peer", "crossover", "evidence"} or not isinstance(ref.get("parent_id"), str):
            raise ProtocolError("invalid_parent_ref_role")
        aid = ref["parent_id"]
        if aid in ids: raise ProtocolError("duplicate_parent_ref")
        ids.add(aid); roles.add(role)
    if root_count and (len(refs) != 1 or ids): raise ProtocolError("root_parent_ref_must_be_exclusive")
    if "crossover" in roles and len(ids) < 2: raise ProtocolError("crossover_requires_multiple_parents")


def validate_events(events: list[dict[str, Any]]) -> bool:
    if not events: raise ProtocolError("empty_event_stream")
    for e in events: _structural_validate(e)
    if Draft202012Validator is not None:
        validator = Draft202012Validator(_schema())
        for i, e in enumerate(events):
            errors = sorted(validator.iter_errors(e), key=lambda x: list(x.path))
            if errors: raise ProtocolError(f"schema_event_{i}:{errors[0].message}")
    boots = [e for e in events if e.get("event_kind") == "protocol_bootstrap"]
    if len(boots) != 1: raise ProtocolError("exactly_one_bootstrap")
    boot, run_id = boots[0], boots[0]["run_id"]
    if boot["producer"]["role"] not in {"framework", "recorder"}: raise ProtocolError("bootstrap_authority")
    event_ids: dict[str, dict[str, Any]] = {}; idem: dict[str, str] = {}
    for e in events:
        if e["run_id"] != run_id: raise ProtocolError("run_id_mismatch")
        eid = e["event_id"]
        if eid in event_ids and event_ids[eid] != e: raise ProtocolError(f"duplicate_event_conflict:{eid}")
        event_ids[eid] = e
        key = e.get("idempotency_key")
        if key and key in idem and idem[key] != eid: raise ProtocolError(f"idempotency_conflict:{key}")
        if key: idem[key] = eid
    arts = _artifact_index(events)
    _check_artifact_cycles(arts)
    graph = {e["event_id"]: set(e.get("causal_parent_events", [])) for e in events}
    for eid, parents in graph.items():
        if eid in parents: raise ProtocolError(f"event_cycle:{eid}")
        if not parents <= set(graph): raise ProtocolError(f"missing_causal_parent:{eid}")
        child_scope = event_ids[eid].get("scope", {})
        for parent_id in parents:
            parent_scope = event_ids[parent_id].get("scope", {})
            if child_scope.get("run_id") != parent_scope.get("run_id"):
                raise ProtocolError("causal_parent_run_id_mismatch")
            if child_scope.get("session_id") != parent_scope.get("session_id"):
                allowed_parent = child_scope.get("parent_scope")
                if allowed_parent not in {parent_id, parent_scope.get("scope_id")}:
                    raise ProtocolError("causal_parent_scope_mismatch")
    done: set[str] = set()
    def visit(node: str, active: set[str]) -> None:
        if node in active: raise ProtocolError(f"event_cycle:{node}")
        if node in done: return
        active.add(node)
        for parent in graph[node]: visit(parent, active)
        active.remove(node); done.add(node)
    for node in graph: visit(node, set())
    declared = boot.get("bootstrap", {}).get("stage_modes", {})
    for e in events:
        if e.get("event_kind") == "protocol_bootstrap": continue
        stage, mode, impl = e["stage"], e["stage"]["mode"], e["implementation"]
        if stage["id"] not in STAGES: raise ProtocolError("bad_stage")
        if stage["id"] in declared and declared[stage["id"]] != mode: raise ProtocolError("undeclared_stage_mode")
        if impl.get("mode") and impl["mode"] != mode: raise ProtocolError("mode_mismatch")
        if not impl.get("module"): raise ProtocolError("implementation_module_required")
        if mode == "default" and impl.get("compliance") not in {"declared", "verified"}:
            raise ProtocolError("default_requires_declared_implementation")
        if mode == "custom" and impl.get("compliance") not in {"declared", "verified", "unverified"}:
            raise ProtocolError("custom_requires_compliance_status")
        life = e["lifecycle"]
        if e["event_kind"] == "stage_started" and life != "running": raise ProtocolError("started_must_be_running")
        if e["event_kind"] == "stage_failed" and life != "failed": raise ProtocolError("failed_event_lifecycle")
        if life == "failed" and "failure" not in e: raise ProtocolError("failed_without_failure")
        if life != "failed" and "failure" in e: raise ProtocolError("failure_on_nonfailed")
        failure = e.get("failure")
        if failure:
            infra = {"delegate_error", "evaluator_error", "recorder_error", "infrastructure_unavailable"}
            if failure["class"] in infra:
                if failure["subject_or_infrastructure"] != "infrastructure" or e["evidence"] != "unobserved":
                    raise ProtocolError("infrastructure_failure_must_be_unobserved")
            elif failure["subject_or_infrastructure"] != "subject" or e["evidence"] != "measured":
                raise ProtocolError("subject_failure_must_be_measured")
        if mode == "delegated":
            for key in ("delegate_id", "capabilities", "timeout_ms", "request_artifact_id", "response_artifact_id"):
                if not impl.get(key): raise ProtocolError(f"delegated_missing:{key}")
            if impl["delegate_id"] not in {d["delegate_id"] for d in boot["bootstrap"].get("delegates", [])}:
                raise ProtocolError("delegate_not_declared")
            for key in ("request_artifact_id", "response_artifact_id"):
                if impl[key] not in arts:
                    raise ProtocolError(f"delegated_{key}_unresolved")
        if mode == "skip":
            if not e.get("reason") or not e.get("preconditions") or not e.get("invariants"):
                raise ProtocolError("skip_contract_missing")
            if e["evidence"] == "measured" and not impl.get("independent_measurement_refs"):
                raise ProtocolError("skip_measured_without_independent_evidence")
            if any(ref not in arts for ref in impl.get("independent_measurement_refs", [])):
                raise ProtocolError("skip_measurement_ref_unresolved")
        payload = e["payload"]; pk = payload["payload_kind"]
        if pk not in PAYLOAD_KEYS: raise ProtocolError(f"unknown_payload:{pk}")
        if set(payload) - PAYLOAD_KEYS[pk]: raise ProtocolError(f"untyped_payload:{pk}")
        if pk in PAYLOAD_STAGES and stage["id"] not in PAYLOAD_STAGES[pk]:
            raise ProtocolError(f"payload_stage_mismatch:{pk}")
        if pk == "context_selection": _check_parent_refs(payload)
        if pk == "context_selection":
            parent_ids = [r["parent_id"] for r in payload.get("parent_refs", []) if "parent_id" in r]
            if set(parent_ids) - set(arts):
                raise ProtocolError(f"context_parent_unresolved:{sorted(set(parent_ids) - set(arts))[0]}")
        input_ids = [a["artifact_id"] for a in e.get("input_artifacts", [])]
        if pk == "feedback":
            _require_artifact_refs(payload.get("source_artifact_ids", []), arts=arts,
                                   listed=input_ids, label="feedback_source")
        if pk == "selection_result":
            _require_artifact_refs(payload.get("basis", []), arts=arts,
                                   listed=input_ids, label="selection_basis")
            candidate_ids = set(payload.get("selected_candidate_ids", []))
            known_candidates = {aid for aid, a in arts.items() if a.get("kind") == "candidate"}
            if candidate_ids - known_candidates:
                raise ProtocolError(f"selection_candidate_unresolved:{sorted(candidate_ids - known_candidates)[0]}")
            later = payload.get("later_eval_ref")
            if later is not None and later not in event_ids and later not in arts:
                raise ProtocolError("selection_later_eval_unresolved")
        if pk == "feedback" and "feedback_consumers" in payload:
            consumers = payload["feedback_consumers"]
            if not isinstance(consumers, list) or len(set(consumers)) != len(consumers):
                raise ProtocolError("feedback_consumers_must_be_unique")
        if pk == "execution":
            if payload.get("training_scope") and not e["scope"].get("parent_scope"):
                raise ProtocolError("training_scope_requires_parent_scope")
            if payload["execution_mode"] == "replay":
                replay_kind = payload.get("replay_kind")
                if not replay_kind or not payload.get("source_trace_ids"):
                    raise ProtocolError("replay_execution_contract")
                source_ids = set(payload["source_trace_ids"])
                if not source_ids <= set(arts) | set(event_ids):
                    raise ProtocolError(f"replay_source_unresolved:{sorted(source_ids - (set(arts) | set(event_ids)))[0]}")
                input_ids = {a["artifact_id"] for a in e.get("input_artifacts", [])}
                if not source_ids <= input_ids:
                    raise ProtocolError("replay_source_not_declared_input")
                for source_id in source_ids & set(arts):
                    if arts[source_id].get("kind") not in {"trace", "experience", "replay"}:
                        raise ProtocolError("replay_source_wrong_kind")
                if replay_kind == "stored_counterfactual" and payload.get("new_execution") is not False:
                    raise ProtocolError("replay_execution_contract")
                if replay_kind in {"executed_smoke", "executed_benchmark"} and payload.get("new_execution") is not True:
                    raise ProtocolError("replay_execution_contract")
            elif payload.get("replay_kind") or payload.get("source_trace_ids"):
                raise ProtocolError("online_replay_fields")
        if pk == "study_evaluation" and payload.get("evaluation_completeness") == "heldout":
            raise ProtocolError("study_payload_claims_heldout")
        if pk in {"study_evaluation", "heldout_evaluation"}:
            candidates = set(payload.get("candidate_ids", []))
            known_candidates = {aid for aid, a in arts.items() if a.get("kind") == "candidate"}
            if candidates - known_candidates:
                raise ProtocolError(f"evaluation_candidate_unresolved:{sorted(candidates - known_candidates)[0]}")
        if pk == "heldout_evaluation" and e["evidence"] != "measured":
            raise ProtocolError("heldout_evaluation_must_be_measured")
        producer_role = e["producer"]["role"]
        if pk == "validation" and producer_role == "method" and e["evidence"] == "measured":
            raise ProtocolError("method_forged_validation")
        if pk == "deployment_fact" and producer_role not in {"framework", "deployer", "recorder"}:
            raise ProtocolError("method_forged_deployment")
        if pk == "selection_result" and producer_role not in {"framework", "method", "meta_agent", "delegate", "recorder"}:
            raise ProtocolError("selection_producer")
        if pk == "study_evaluation" and producer_role not in {"framework", "evaluator", "recorder", "delegate"}:
            raise ProtocolError("study_score_authority")
        if pk == "heldout_evaluation" and producer_role not in TRUSTED_FACT_ROLES:
            raise ProtocolError("heldout_score_authority")
        if pk == "study_evaluation" and not _delegate_authorized(boot, e["producer"], "evaluate"):
            raise ProtocolError("study_delegate_authority")
        if pk == "heldout_evaluation" and not _delegate_authorized(boot, e["producer"], "heldout_evaluate"):
            raise ProtocolError("heldout_delegate_authority")
        for a in e.get("artifacts", []) + e.get("output_artifacts", []):
            allowed = ARTIFACT_AUTHORITY.get(a["kind"])
            if allowed and a["producer"]["role"] not in allowed:
                raise ProtocolError(f"artifact_producer_authority:{a['kind']}")
            if a["kind"] in {"study_score", "heldout_score"} and not _delegate_authorized(
                    boot, a["producer"], "evaluate" if a["kind"] == "study_score" else "heldout_evaluate"):
                raise ProtocolError(f"artifact_delegate_authority:{a['kind']}")
            if a["kind"] == "study_score":
                if (a.get("score_kind") != "study" or a["visibility"] == "heldout_private"
                        or a["producer"]["role"] not in {"framework", "evaluator", "recorder", "delegate"}):
                    raise ProtocolError("study_visibility_or_authority")
            if a["kind"] == "heldout_score":
                if a["producer"]["role"] not in TRUSTED_FACT_ROLES:
                    raise ProtocolError("heldout_producer")
                if (a["visibility"] != "heldout_private" or a.get("score_kind") != "heldout"
                        or (a.get("split_visibility") is not None and a["split_visibility"] != "heldout_private")):
                    raise ProtocolError("heldout_visibility_or_kind")
            if a["kind"] == "candidate":
                root, parents = a.get("root") is True, a.get("parents", [])
                if root == bool(parents): raise ProtocolError("candidate_root_parent_contradiction")
                if a.get("lineage") and {x["parent_id"] for x in a["lineage"]} - set(parents):
                    raise ProtocolError("lineage_parent_mismatch")
                if any(x.get("role") == "crossover" for x in a.get("lineage", [])) and len(parents) < 2:
                    raise ProtocolError("crossover_requires_multiple_parents")
                if any(x.get("role") == "root" for x in a.get("lineage", [])):
                    raise ProtocolError("root_lineage_must_be_root_flag")
        for a in e.get("artifacts", []) + e.get("input_artifacts", []) + e.get("output_artifacts", []):
            if "scope" in a:
                if a["scope"].get("run_id") != e["run_id"]:
                    raise ProtocolError("artifact_scope_run_id_mismatch")
                for key in ("session_id", "iteration_id", "branch_id"):
                    if key in a["scope"] and a["scope"].get(key) != e["scope"].get(key):
                        raise ProtocolError(f"artifact_scope_{key}_mismatch")
            refs = set(a.get("parents", [])) | {x["parent_id"] for x in a.get("lineage", []) if "parent_id" in x}
            missing = refs - set(arts)
            if missing: raise ProtocolError(f"missing_artifact_parent:{sorted(missing)[0]}")
        if pk == "deployment_fact":
            status = payload["status"]; deployed = payload["deployed_candidate_ids"]
            known_candidates = {aid for aid, a in arts.items() if a.get("kind") == "candidate"}
            if status in {"deployed", "rolled_back"} and not deployed:
                raise ProtocolError("deployment_status_requires_candidates")
            if status == "none" and deployed:
                raise ProtocolError("none_deployment_has_candidates")
            if set(deployed) - known_candidates:
                raise ProtocolError("deployment_candidate_unresolved")
            snapshot_hashes = {a.get("sha256") for a in arts.values()}
            for key in ("from_snapshot", "to_snapshot"):
                _hash(payload[key], key)
                if payload[key] not in snapshot_hashes:
                    raise ProtocolError(f"deployment_{key}_unresolved")
            if status == "rolled_back" and not payload.get("rollback_ref"):
                raise ProtocolError("rollback_requires_ref")
            if payload.get("rollback_ref") and payload["rollback_ref"] not in event_ids and payload["rollback_ref"] not in arts:
                raise ProtocolError("rollback_ref_unresolved")
        if pk == "reconciliation":
            refs = set(payload["reconciles_event_ids"])
            if e["event_id"] in refs or not refs <= set(event_ids):
                raise ProtocolError("reconciliation_reference_missing")
        if stage["id"] in {"organize_feedback", "propose_changes", "select_deploy"}:
            adaptation_artifacts = (e.get("input_artifacts", []) + e.get("artifacts", [])
                                    + e.get("output_artifacts", []))
            for a in adaptation_artifacts:
                chain = {a["artifact_id"]} | _ancestors(a["artifact_id"], arts)
                if any(arts.get(x, {}).get("kind") == "heldout_score" for x in chain):
                    raise ProtocolError("heldout_taint_to_adaptation")
        if pk in {"feedback", "selection_result"}:
            adaptation_refs = (payload.get("source_artifact_ids", []) if pk == "feedback"
                               else payload.get("basis", []))
            for aid in adaptation_refs:
                chain = {aid} | _ancestors(aid, arts)
                if any(arts.get(x, {}).get("kind") == "heldout_score" for x in chain):
                    raise ProtocolError("heldout_taint_to_adaptation")
    return True
