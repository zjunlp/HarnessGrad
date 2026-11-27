"""D1 source and trigger contracts for the offline learning lane."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from harnessgrad.personal.contracts import SourceEvent, digest, identifier
from harnessgrad.personal.evidence import EvidenceSelection
from harnessgrad.personal.evolution_contracts import Scope

SourceStatus = Literal['ready', 'unavailable', 'refused', 'unchecked']
TriggerStatus = Literal['triggered', 'skipped', 'deferred']


class SourceConflict(ValueError):
    """A source no longer matches its stored read position."""


# D's domain spelling remains import-compatible while the actual scope DTO is
# owned by the shared C/D boundary.
LearningScope = Scope


@dataclass(frozen=True)
class LearningPolicy:
    scope: LearningScope
    revision: int = 0
    enabled: bool = False
    source_ids: tuple[str, ...] = ()
    cooldown_seconds: int = 0
    max_reflections_per_day: int = 0

    def __post_init__(self):
        if type(self.enabled) is not bool or not isinstance(self.source_ids, tuple):
            raise ValueError('expected an explicit learning switch and source tuple')
        for source_id in self.source_ids:
            identifier(source_id)
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError('learning sources must be unique')
        for value in (self.revision, self.cooldown_seconds, self.max_reflections_per_day):
            if type(value) is not int or not 0 <= value < 2**31:
                raise ValueError('learning policy counts must be nonnegative integers')


@dataclass(frozen=True)
class LearningPolicyUpdate:
    scope: LearningScope
    expected_revision: int
    enabled: bool
    source_ids: tuple[str, ...]
    cooldown_seconds: int
    max_reflections_per_day: int
    idempotency_key: str
    confirmation_ref: str | None = None

    def __post_init__(self):
        identifier(self.idempotency_key)
        if self.confirmation_ref is not None:
            identifier(self.confirmation_ref)
        self.next_policy()

    def next_policy(self) -> LearningPolicy:
        if type(self.expected_revision) is not int or not 0 <= self.expected_revision < 2**31 - 1:
            raise ValueError('invalid learning policy revision')
        return LearningPolicy(
            self.scope, self.expected_revision + 1, self.enabled, self.source_ids,
            self.cooldown_seconds, self.max_reflections_per_day,
        )


@dataclass(frozen=True)
class SourceCapability:
    source_id: str
    kind: Literal['selected_import', 'selected_thread', 'authorized_hook']
    status: SourceStatus
    reason_code: str | None = None


@dataclass(frozen=True)
class SourceItem:
    """One normalized event with selection and task metadata from its adapter."""

    event: SourceEvent
    selection: EvidenceSelection | None = None
    request_kind: Literal['reusable', 'one_off', 'unspecified'] = 'unspecified'
    task_boundary: bool = False
    own_event: bool = False
    event_sha256: str = field(init=False)

    def __post_init__(self):
        if (self.request_kind not in ('reusable', 'one_off', 'unspecified')
                or type(self.task_boundary) is not bool or type(self.own_event) is not bool):
            raise ValueError('invalid source task metadata')
        fingerprint = digest(self.event.as_dict())
        object.__setattr__(self, 'event_sha256', fingerprint)
        if self.selection is not None:
            selection = self.selection
            if ((selection.source_instance, selection.session_id, selection.event_id, selection.sha256)
                    != (self.source_id, self.session_id, self.event_id, fingerprint)
                    or selection.end > len(self.event.text)):
                raise ValueError('selected evidence does not match its source event')

    @property
    def source_id(self) -> str:
        return self.event.source_instance

    @property
    def session_id(self) -> str:
        return self.event.session_id

    @property
    def event_id(self) -> str:
        return self.event.event_id

    @property
    def completeness(self) -> Literal['complete', 'partial']:
        return 'partial' if self.event.truncated else 'complete'


@dataclass(frozen=True)
class SourcePage:
    events: tuple[SourceItem, ...]
    next_cursor: str | None
    has_more: bool


class LearningSource(Protocol):
    """Redeliver a classified event with its original identity, bytes, and any existing selection."""

    def probe_source(self, scope: LearningScope, source_id: str) -> SourceCapability: ...

    def read_source(
        self, scope: LearningScope, source_id: str, *, cursor: str | None, limit: int = 50,
    ) -> SourcePage: ...


@dataclass(frozen=True)
class TriggerInput:
    scope: LearningScope
    evidence_refs: tuple[str, ...]
    policy: LearningPolicy
    remaining_budget: int
    last_trigger_at: datetime | None
    now: datetime


@dataclass(frozen=True)
class TriggerDecision:
    status: TriggerStatus
    evidence_refs: tuple[str, ...] = ()
    reason_code: str | None = None


@dataclass(frozen=True)
class DecisionReceipt:
    """Durable outcome and optional audit excerpt, separate from trigger eligibility."""

    seq: int
    source_id: str
    session_id: str
    event_id: str
    event_sha256: str
    decision: TriggerDecision
    evidence_ref: str | None = None


@dataclass(frozen=True)
class PollResult:
    cursor: str | None
    receipts: tuple[DecisionReceipt, ...] = ()
    duplicates: int = 0
    has_more: bool = False
    reason_code: str | None = None
