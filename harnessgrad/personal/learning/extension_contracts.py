"""Declarative D4 candidates and temporary projections of the v1 C ports."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from harnessgrad.personal.contracts import identifier
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.maintenance_contracts import OperationRef, OperationView
from harnessgrad.personal.learning.reflection_contracts import ArtifactRef, JobRef, sha256_value, unique_ids

ExtensionKind = Literal['tool', 'plugin', 'workflow']


def _validate_provider(kind: ExtensionKind, provider_id: str, version: str, fingerprint: str) -> None:
    if kind not in ('tool', 'plugin', 'workflow'):
        raise ValueError('unknown extension kind')
    identifier(provider_id)
    if not isinstance(version, str) or len(version) > 128 or not version.strip() or not version.isprintable():
        raise ValueError('invalid extension provider version')
    sha256_value(fingerprint)


@dataclass(frozen=True)
class TrustedProvider:
    """Pinned by trusted configuration, independent of all candidate input."""

    scope: LearningScope
    kind: ExtensionKind
    provider_id: str
    provider_version: str
    manifest_sha256: str
    capability_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    required_capability_ids: tuple[str, ...] = ()

    def __post_init__(self):
        _validate_provider(self.kind, self.provider_id, self.provider_version, self.manifest_sha256)
        unique_ids(self.capability_ids)
        unique_ids(self.scenario_ids)
        if self.required_capability_ids != ():
            unique_ids(self.required_capability_ids)
        if not set(self.required_capability_ids) <= set(self.capability_ids):
            raise ValueError('required extension capabilities must be allowed by the provider')


@dataclass(frozen=True)
class ExtensionSpec:
    scope: LearningScope
    kind: ExtensionKind
    provider_id: str
    provider_version: str
    manifest_sha256: str
    capability_ids: tuple[str, ...]
    scenario_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self):
        _validate_provider(self.kind, self.provider_id, self.provider_version, self.manifest_sha256)
        unique_ids(self.capability_ids)
        unique_ids(self.evidence_refs)
        identifier(self.scenario_id)
        object.__setattr__(self, 'capability_ids', tuple(sorted(self.capability_ids)))
        object.__setattr__(self, 'evidence_refs', tuple(sorted(self.evidence_refs)))

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExtensionRequest:
    scope: LearningScope
    candidate_ref: str
    candidate_sha256: str
    execution_grant_ref: str
    idempotency_key: str

    def __post_init__(self):
        for value in (self.candidate_ref, self.execution_grant_ref, self.idempotency_key):
            identifier(value)
        sha256_value(self.candidate_sha256)


@dataclass(frozen=True)
class ExtensionInvocationRequest:
    job: JobRef
    report_ref: str
    input_ref: str
    input_sha256: str
    host_binding_ref: str
    invocation_grant_ref: str
    idempotency_key: str

    def __post_init__(self):
        for value in (self.report_ref, self.input_ref, self.host_binding_ref,
                      self.invocation_grant_ref, self.idempotency_key):
            identifier(value)
        sha256_value(self.input_sha256)


@dataclass(frozen=True)
class RunRecord:
    run_ref: str
    job: JobRef
    scenario_id: str
    phase: Literal['baseline', 'candidate', 'extension_invoke']
    attempt: int
    scenario_sha256: str
    snapshot_sha256: str
    prepared_sha256: str | None
    provider_version: str | None
    command: tuple[str, ...]
    exit_code: int | None
    log_ref: str | None
    log_truncated: bool
    status: Literal['executed', 'timeout', 'cancelled', 'unavailable', 'failed']
    judge_result: Literal['met', 'not_met', 'unknown']
    reason_code: str | None
    fixture: bool
    operation: OperationRef | None = None


@dataclass(frozen=True)
class ComparisonReport:
    report_ref: str
    job: JobRef
    candidate_id: str | None
    baseline_run_refs: tuple[str, ...]
    candidate_run_refs: tuple[str, ...]
    comparable: bool
    verdict: Literal['not_reproduced', 'retest_passed', 'retest_failed', 'not_comparable']
    reason_code: str | None
    fixture: bool
    candidate_sha256: str | None = None


@dataclass(frozen=True)
class ExtensionValidation:
    report: ComparisonReport
    baseline: tuple[RunRecord, ...]
    candidate: tuple[RunRecord, ...]


@dataclass(frozen=True)
class ExtensionInvocation:
    operation: OperationView
    run: RunRecord | None


class ExtensionPort(Protocol):
    """C's scoped registry, validation queue and invocation boundary."""

    def register_extension_candidate(self, request: ExtensionSpec) -> ArtifactRef: ...

    def submit_extension(self, request: ExtensionRequest) -> JobRef: ...

    def request_extension_invocation(self, request: ExtensionInvocationRequest) -> OperationRef: ...

    def read_operation(self, operation: OperationRef) -> OperationView: ...

    def read_comparison(self, job: JobRef, report_ref: str) -> ComparisonReport: ...

    def read_run(self, job: JobRef, run_ref: str) -> RunRecord:
        """For invocations, return the executing operation and input digest (snapshot_sha256)."""
        ...
