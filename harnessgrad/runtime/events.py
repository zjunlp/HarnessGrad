"""Typed values for the common eight-stage event envelope.

Dataclasses are used instead of a third-party model library so adapters can use
the contract in the method sandbox.  ``to_dict`` returns JSON-compatible data;
the validator remains authoritative for cross-event invariants.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# JSON event envelopes are intentionally plain dictionaries at the boundary;
# this alias gives typed callers a stable annotation without hiding extensions.
ProtocolEvent = dict[str, Any]


def sha256_bytes(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode()
    return "sha256:" + hashlib.sha256(value).hexdigest()


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class StageID(_StrEnum):
    SELECT_CONTEXT = "select_context"
    EXECUTE = "execute"
    ORGANIZE_FEEDBACK = "organize_feedback"
    PROPOSE_CHANGES = "propose_changes"
    BUILD_VALIDATE = "build_validate"
    EVALUATE_CANDIDATES = "evaluate_candidates"
    SELECT_DEPLOY = "select_deploy"
    STOP_CONTINUE = "stop_continue"


class ImplementationMode(_StrEnum):
    DEFAULT = "default"
    CUSTOM = "custom"
    DELEGATED = "delegated"
    SKIP = "skip"


class Lifecycle(_StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Evidence(_StrEnum):
    MEASURED = "measured"
    UNOBSERVED = "unobserved"


class Role(_StrEnum):
    FRAMEWORK = "framework"
    TASK_AGENT = "task_agent"
    META_AGENT = "meta_agent"
    METHOD = "method"
    DELEGATE = "delegate"
    EVALUATOR = "evaluator"
    DEPLOYER = "deployer"
    RECORDER = "recorder"


class ArtifactKind(_StrEnum):
    # Content variants are first-class kinds as well as being available through
    # ``content_kind``.  This lets adapters publish a patch/full-file/tree or a
    # prompt/skill/memory/policy/checkpoint without inventing a method-specific
    # artifact name.
    PATCH = "patch"
    FULL_FILE = "full_file"
    FULL_FILE_DASH = "full-file"
    FILE = "file"
    TREE = "tree"
    CONFIG = "config"
    TASK_SET = "task_set"
    PARENT_STATE = "parent_state"
    EXPERIENCE = "experience"
    FEEDBACK = "feedback"
    PROPOSAL = "proposal"
    CANDIDATE = "candidate"
    VALIDATION = "validation"
    STUDY_SCORE = "study_score"
    HELDOUT_SCORE = "heldout_score"
    SELECTION = "selection"
    DEPLOYMENT = "deployment"
    STOP_DECISION = "stop_decision"
    ARCHIVE = "archive"
    TRACE = "trace"
    CLAIM = "claim"
    LEGACY_RESPONSE = "legacy_response"
    POST_RUN_ANALYSIS = "post_run_analysis"
    CHECKPOINT = "checkpoint"
    TREE_SNAPSHOT = "tree_snapshot"
    PROMPT = "prompt"
    SKILL = "skill"
    MEMORY = "memory"
    POLICY = "policy"
    PARAMETER_UPDATE = "parameter_update"


class ContentKind(_StrEnum):
    PATCH = "patch"
    FULL_FILE = "full_file"
    FULL_FILE_DASH = "full-file"
    FILE = "file"  # accepted for older adapters
    TREE = "tree"
    CONFIG = "config"
    PROMPT = "prompt"
    SKILL = "skill"
    MEMORY = "memory"
    POLICY = "policy"
    CHECKPOINT = "checkpoint"
    PARAMETER_UPDATE = "parameter_update"
    NONE = "none"


@dataclass(frozen=True)
class Owner:
    role: Role | str
    id: str
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"role": str(self.role), "id": self.id}
        if self.version is not None:
            out["version"] = self.version
        return out


@dataclass(frozen=True)
class Scope:
    run_id: str
    session_id: str
    iteration_id: str
    branch_id: str
    candidate_id: str | None = None
    candidate_ids: tuple[str, ...] = ()
    task_id: str | None = None
    trial_id: str | None = None
    parent_scope: str | None = None
    stream_id: str | None = None
    batch_id: str | None = None
    deployment_epoch: str | None = None
    scope_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"run_id": self.run_id, "session_id": self.session_id,
                               "iteration_id": self.iteration_id, "branch_id": self.branch_id}
        for key in ("candidate_id", "task_id", "trial_id", "parent_scope", "stream_id",
                    "batch_id", "deployment_epoch", "scope_id"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.candidate_ids:
            out["candidate_ids"] = list(self.candidate_ids)
        return out


@dataclass(frozen=True)
class ParentRef:
    """A typed parent edge.

    A root context is represented by ``ParentRef(None, "root")``.  Root is a
    marker, not an edge to a synthetic artifact, so it cannot be combined with
    donor/base/crossover refs in the same context selection.
    """

    artifact_id: str | None = None
    role: str = "base"  # base, donor, peer, crossover, evidence

    def __post_init__(self) -> None:
        if self.role == "root":
            if self.artifact_id not in (None, ""):
                raise ValueError("root_parent_ref_has_no_parent_id")
        elif self.role not in {"base", "donor", "peer", "crossover", "evidence"}:
            raise ValueError("invalid_parent_ref_role")
        elif not self.artifact_id:
            raise ValueError("nonroot_parent_ref_requires_parent_id")

    def to_dict(self) -> dict[str, str]:
        if self.role == "root":
            return {"role": self.role}
        return {"parent_id": str(self.artifact_id), "role": self.role}


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    kind: ArtifactKind | str
    sha256: str
    visibility: str
    producer: Owner
    parents: tuple[str, ...] = ()
    lineage: tuple[ParentRef, ...] = ()
    root: bool | None = None
    score_kind: str | None = None
    split_visibility: str | None = None
    scorer_revision: str | None = None
    content_kind: ContentKind | str | None = None
    uri: str | None = None
    scope: Scope | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"artifact_id": self.artifact_id, "kind": str(self.kind),
                               "sha256": self.sha256, "visibility": self.visibility,
                               "producer": self.producer.to_dict()}
        if self.uri is not None: out["uri"] = self.uri
        if self.scope is not None: out["scope"] = self.scope.to_dict()
        if self.parents: out["parents"] = list(self.parents)
        if self.lineage: out["lineage"] = [x.to_dict() for x in self.lineage]
        if self.root is not None: out["root"] = self.root
        for key in ("score_kind", "split_visibility", "scorer_revision", "content_kind"):
            value = getattr(self, key)
            if value is not None: out[key] = str(value)
        return out


def artifact(artifact_id: str, kind: ArtifactKind | str, content: bytes | str = b"",
             *, producer: Owner | None = None, visibility: str = "public_record",
             parents: Iterable[str] = (), root: bool | None = None,
             lineage: Iterable[ParentRef] = (), score_kind: str | None = None,
             content_kind: ContentKind | str | None = None, scope: Scope | None = None,
             **kwargs: Any) -> ArtifactRef:
    """Construct an immutable reference and hash content when supplied."""
    if producer is None:
        producer = Owner(Role.FRAMEWORK, "framework")
    digest = content if isinstance(content, str) and content.startswith("sha256:") else sha256_bytes(content)
    return ArtifactRef(artifact_id, kind, digest, visibility, producer,
                       tuple(parents), tuple(lineage), root, score_kind=score_kind,
                       content_kind=content_kind, scope=scope, **kwargs)


@dataclass(frozen=True)
class Stage:
    id: StageID | str
    invocation_id: str
    mode: ImplementationMode | str
    repeat_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"id": str(self.id), "invocation_id": self.invocation_id,
                "mode": str(self.mode), "repeat_index": self.repeat_index}


@dataclass(frozen=True)
class StageResult:
    invocation_id: str
    stage_id: StageID | str
    status: Lifecycle | str
    evidence: Evidence | str
    outputs: tuple[ArtifactRef, ...] = ()
    invariants: tuple[str, ...] = ()
    claims: Mapping[str, Any] = field(default_factory=dict)
    failure: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"invocation_id": self.invocation_id, "stage_id": str(self.stage_id),
               "status": str(self.status), "evidence": str(self.evidence),
               "outputs": [a.to_dict() for a in self.outputs],
               "invariants": list(self.invariants), "claims": dict(self.claims)}
        if self.failure is not None: out["failure"] = dict(self.failure)
        return out


class EventValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def event(*, run_id: str, event_id: str, scope: Scope, stage: Stage, lifecycle: Lifecycle | str,
          evidence: Evidence | str, owner: Owner, producer: Owner, implementation: Mapping[str, Any],
          payload: Mapping[str, Any], event_kind: str = "stage_completed",
          artifacts: Sequence[ArtifactRef] = (), input_artifacts: Sequence[ArtifactRef] = (),
          output_artifacts: Sequence[ArtifactRef] = (), attempt_id: str | None = None,
          attempt: int = 1, idempotency_key: str | None = None, started_at: str | None = None,
          finished_at: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"protocol_version": "1.0", "run_id": run_id,
        "event_id": event_id, "event_kind": event_kind, "scope": scope.to_dict(),
        "stage": stage.to_dict(), "lifecycle": str(lifecycle), "evidence": str(evidence),
        "owner": owner.to_dict(), "producer": producer.to_dict(),
        "implementation": dict(implementation), "artifacts": [a.to_dict() for a in artifacts],
        "input_artifacts": [a.to_dict() for a in input_artifacts],
        "output_artifacts": [a.to_dict() for a in output_artifacts],
        "result_kind": "artifact" if output_artifacts else "empty", "attempt_id": attempt_id or f"attempt-{event_id}",
        "attempt": attempt, "idempotency_key": idempotency_key or f"idem-{event_id}",
        "started_at": started_at or _now(), "finished_at": finished_at or _now(),
        "payload": dict(payload)}
    out.update({key: value for key, value in extra.items() if value is not None})
    return out


class EventRecorder:
    """Append-only event recorder with retry-safe idempotency.

    A retry with an existing idempotency key returns the original event when its
    canonical JSON is equal.  A different payload raises instead of silently
    overwriting the first observation.  Late events are accepted and can be
    reconciled explicitly with ``reconcile``.
    """

    def __init__(self, run_id: str, session_id: str, *, producer: Owner | None = None,
                 stream: str | Path | None = None, protocol_version: str = "1.0") -> None:
        self.run_id, self.session_id = run_id, session_id
        self.producer = producer or Owner(Role.FRAMEWORK, "framework")
        self.protocol_version = protocol_version
        self.stream = Path(stream) if stream else None
        self._events: list[dict[str, Any]] = []
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._event_ids: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    def _append(self, value: dict[str, Any]) -> dict[str, Any]:
        value = json.loads(json.dumps(value, sort_keys=True))
        key = value.get("idempotency_key")
        with self._lock:
            if key and key in self._idempotency:
                old = self._idempotency[key]
                comparable_old = {k: v for k, v in old.items() if k not in {"started_at", "finished_at"}}
                comparable_new = {k: v for k, v in value.items() if k not in {"started_at", "finished_at"}}
                if comparable_old != comparable_new:
                    raise EventValidationError(f"idempotency_conflict:{key}")
                return json.loads(json.dumps(old))
            event_id = value.get("event_id")
            if event_id in self._event_ids:
                old_by_id = self._event_ids[event_id]
                comparable_old = {k: v for k, v in old_by_id.items() if k not in {"started_at", "finished_at"}}
                comparable_new = {k: v for k, v in value.items() if k not in {"started_at", "finished_at"}}
                if comparable_old != comparable_new:
                    raise EventValidationError(f"duplicate_event_conflict:{event_id}")
                return json.loads(json.dumps(old_by_id))
            self._events.append(value)
            self._event_ids[event_id] = value
            if key: self._idempotency[key] = value
            if self.stream:
                self.stream.parent.mkdir(parents=True, exist_ok=True)
                with self.stream.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            return json.loads(json.dumps(value))

    def bootstrap(self, *, profile_opt_in: str = "legacy.compatibility.v1", stage_modes: Mapping[str, str] | None = None,
                  task_set_sha: str | None = None, evidence_mode: str = "declared_traces",
                  budget: Mapping[str, Any] | None = None, delegates: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        value = {"protocol_version": self.protocol_version, "run_id": self.run_id,
                 "event_id": f"bootstrap-{self.run_id}", "event_kind": "protocol_bootstrap",
                 "producer": self.producer.to_dict(), "bootstrap": {
                     "profile_opt_in": profile_opt_in,
                     "stage_modes": dict(stage_modes or {}),
                     "task_plan": {"task_set_sha": task_set_sha or sha256_bytes(b"unobserved-task-set")},
                     "evidence_mode": evidence_mode,
                     "budget": dict(budget or {"unit": "generations", "limit": 1}),
                     "delegates": [dict(d) for d in delegates]}}
        return self._append(value)

    def record(self, *, scope: Scope, stage: Stage, payload: Mapping[str, Any], owner: Owner | None = None,
               producer: Owner | None = None, lifecycle: Lifecycle | str = Lifecycle.COMPLETED,
               evidence: Evidence | str = Evidence.MEASURED, implementation: Mapping[str, Any] | None = None,
               **kwargs: Any) -> dict[str, Any]:
        eid = kwargs.pop("event_id", f"ev-{uuid.uuid4().hex}")
        impl = {"module": "hg.runtime.v1", "compliance": "declared", "fidelity": "unknown"}
        impl.update(implementation or {})
        event_kind = kwargs.pop("event_kind", None)
        if event_kind is None:
            event_kind = {"failed": "stage_failed", "running": "stage_started"}.get(str(lifecycle), "stage_completed")
        return self._append(event(run_id=self.run_id, event_id=eid, scope=scope, stage=stage,
            lifecycle=lifecycle, evidence=evidence, owner=owner or self.producer,
            producer=producer or self.producer, implementation=impl, payload=payload,
            event_kind=event_kind, **kwargs))

    def reconcile(self, *, scope: Scope, stage: Stage, event_ids: Sequence[str], reason: str,
                  owner: Owner | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.record(scope=scope, stage=stage,
            payload={"payload_kind": "reconciliation", "reconciles_event_ids": list(event_ids)},
            owner=owner, event_kind="reconciliation", reason=reason,
            evidence=Evidence.MEASURED, **kwargs)
