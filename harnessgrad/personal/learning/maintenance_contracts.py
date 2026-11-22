"""Learning maintenance DTOs and the v1 C operations it consumes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from harnessgrad.improvement.stages import MAX_TEXT_EDIT_BYTES
from harnessgrad.personal.contracts import identifier
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.reflection_contracts import (
    ArtifactPort, CandidateRequest, ExperienceKind, JobRef, SnapshotRef, unique_ids,
)

ItemState = Literal['proposed', 'active', 'disabled', 'archived']
MaintenanceAction = Literal['correct', 'merge', 'disable', 'archive', 'restore']
MaintenanceStatus = Literal['recorded', 'needs_candidate', 'needs_rollback', 'conflict', 'no_change']


@dataclass(frozen=True)
class LearningItem:
    item_id: str
    revision: int
    kind: ExperienceKind
    state: ItemState
    summary: str
    target_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    conditions: tuple[str, ...]
    job: JobRef | None = None
    version_receipt_ref: str | None = None


@dataclass(frozen=True)
class LearningPage:
    items: tuple[LearningItem, ...]
    next_after: str | None
    has_more: bool


@dataclass(frozen=True)
class TaskContext:
    target_ids: tuple[str, ...]
    condition_tags: tuple[str, ...] = ()

    def __post_init__(self):
        unique_ids(self.target_ids)
        if (not isinstance(self.condition_tags, tuple)
                or any(not isinstance(tag, str) or not tag.strip() for tag in self.condition_tags)):
            raise ValueError('task conditions must be explicit text tags')


@dataclass(frozen=True)
class ItemRevision:
    item_id: str
    expected_revision: int

    def __post_init__(self):
        identifier(self.item_id)
        if type(self.expected_revision) is not int or not 1 <= self.expected_revision < 2**63:
            raise ValueError('invalid expected learning item revision')


@dataclass(frozen=True)
class MaintenanceRequest:
    scope: LearningScope
    action: MaintenanceAction
    items: tuple[ItemRevision, ...]
    reason: str
    idempotency_key: str
    correction: str | None = None

    def __post_init__(self):
        identifier(self.idempotency_key)
        if self.action not in ('correct', 'merge', 'disable', 'archive', 'restore'):
            raise ValueError('unknown maintenance action')
        if not isinstance(self.items, tuple) or any(not isinstance(item, ItemRevision) for item in self.items):
            raise ValueError('maintenance requires expected item revisions')
        unique_ids(tuple(item.item_id for item in self.items))
        if (self.action == 'merge' and len(self.items) < 2) or (self.action != 'merge' and len(self.items) != 1):
            raise ValueError('merge needs multiple distinct items; other actions need one')
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason.encode('utf-8')) > 1024:
            raise ValueError('maintenance requires a bounded reason')
        if self.action == 'correct':
            if (not isinstance(self.correction, str) or not self.correction.strip() or '\x00' in self.correction
                    or len(self.correction.encode('utf-8')) > MAX_TEXT_EDIT_BYTES):
                raise ValueError('correction must contain a bounded replacement')
        elif self.correction is not None:
            raise ValueError('replacement text is only accepted for correction')


@dataclass(frozen=True)
class MaintenanceDecision:
    decision_id: str
    status: MaintenanceStatus
    candidate: CandidateRequest | None = None
    job: JobRef | None = None
    restore_version_ref: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class CurrentVersion:
    target_id: str
    version_id: str | None
    content_sha256: str | None


@dataclass(frozen=True)
class RollbackRequest:
    job: JobRef
    restore_version_ref: str
    expected_versions: tuple[CurrentVersion, ...]
    authorization_ref: str
    idempotency_key: str


@dataclass(frozen=True)
class OperationRef:
    job: JobRef
    operation_id: str

    @classmethod
    def from_dict(cls, value: dict) -> OperationRef:
        return cls(JobRef(**value['job']), value['operation_id'])


@dataclass(frozen=True)
class OperationView:
    operation: OperationRef
    kind: str
    status: str
    reason_code: str | None = None
    version_receipt_ref: str | None = None
    load_observation_ref: str | None = None
    run_ref: str | None = None
    # The accepted request's input digest, independent of the returned run.
    invocation_input_sha256: str | None = None


@dataclass(frozen=True)
class VersionEntry:
    target_id: str
    before_sha256: str | None
    after_sha256: str | None
    status: str


@dataclass(frozen=True)
class VersionReceipt:
    receipt_ref: str
    operation: OperationRef
    candidate_id: str | None
    candidate_sha256: str | None
    before_version_ref: str | None
    after_version_ref: str | None
    reason_code: str | None
    entries: tuple[VersionEntry, ...]


class MaintenanceArtifacts(ArtifactPort, Protocol):
    def freeze_snapshot(self, scope: LearningScope, target_ids: tuple[str, ...]) -> SnapshotRef: ...


class VersionPort(Protocol):
    def read_current_versions(
        self, scope: LearningScope, target_ids: tuple[str, ...],
    ) -> tuple[CurrentVersion, ...]: ...

    def read_version_targets(
        self, scope: LearningScope, version_ref: str, target_ids: tuple[str, ...],
    ) -> tuple[CurrentVersion, ...]:
        """Project immutable C package metadata into the identities of its targets."""
        ...

    def request_rollback(self, request: RollbackRequest) -> OperationRef: ...

    def read_operation(self, operation: OperationRef) -> OperationView: ...

    def read_version_receipt(self, job: JobRef, receipt_ref: str) -> VersionReceipt: ...
