"""Public, implementation-free contracts for personal harness evolution.

This module is the shared boundary between the evolution provider (C) and its
callers.  Keep it free of persistence, host, model, desktop and learning
implementation imports so consumers can use the DTOs without acquiring an
implementation dependency.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import re
from typing import Literal, Protocol

from harnessgrad.personal.contracts import identifier

CANDIDATE_SCHEMA = 'harnessgrad.personal.evolution.candidate-request.v1'
MAX_EVENT_PAGE = 50
MAX_SELECTIONS = 200

CandidateIntent = Literal['preference', 'behavior_fix', 'new_capability']
JobStatus = Literal[
    'created', 'collecting', 'reproducing', 'baseline_ready', 'proposing',
    'validating', 'retesting', 'needs_input', 'not_reproduced', 'no_change',
    'retest_passed', 'retest_failed', 'not_comparable', 'cancelled',
    'interrupted', 'failed',
]
JOB_STATUSES = frozenset(JobStatus.__args__)


def sha256_value(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('expected a SHA-256 digest')


def unique_ids(values: tuple[str, ...], *, allow_empty: bool = False,
               limit: int = MAX_SELECTIONS) -> None:
    minimum = 0 if allow_empty else 1
    if not isinstance(values, tuple) or not minimum <= len(values) <= limit:
        raise ValueError('expected a bounded selection')
    for value in values:
        identifier(value)
    if len(set(values)) != len(values):
        raise ValueError('duplicate selection')


def event_cursor(after: int) -> int:
    if type(after) is not int or not 0 <= after < 2**63:
        raise PortRejected('invalid_cursor')
    return after


def _optional_id(value: str | None) -> None:
    if value is not None:
        identifier(value)


def _bounded_texts(values: tuple[str, ...]) -> None:
    if (not isinstance(values, tuple) or len(values) > 32
            or any(not isinstance(value, str) or not value.strip()
                   or len(value.encode('utf-8')) > 1024 for value in values)):
        raise ValueError('expected bounded nonempty text')


@dataclass(frozen=True)
class Scope:
    profile_id: str
    project_id: str

    def __post_init__(self):
        identifier(self.profile_id)
        identifier(self.project_id)

    def key(self) -> tuple[str, str]:
        return self.profile_id, self.project_id


@dataclass(frozen=True)
class SnapshotRef:
    snapshot_ref: str
    snapshot_sha256: str
    target_ids: tuple[str, ...]

    def __post_init__(self):
        identifier(self.snapshot_ref)
        sha256_value(self.snapshot_sha256)
        unique_ids(self.target_ids)


@dataclass(frozen=True)
class ArtifactRef:
    ref: str
    sha256: str
    kind: str

    def __post_init__(self):
        identifier(self.ref)
        sha256_value(self.sha256)
        identifier(self.kind)


@dataclass(frozen=True)
class ArtifactView(ArtifactRef):
    payload: Mapping
    truncated: bool = False

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.payload, Mapping) or type(self.truncated) is not bool:
            raise ValueError('invalid artifact view')


@dataclass(frozen=True)
class CandidateRequest:
    profile_id: str
    project_id: str
    target_ids: tuple[str, ...]
    snapshot_ref: str
    snapshot_sha256: str
    candidate_ref: str
    candidate_sha256: str
    evidence_refs: tuple[str, ...]
    intent: CandidateIntent
    conditions: tuple[str, ...]
    scenario_id: str
    idempotency_key: str
    schema: str = CANDIDATE_SCHEMA

    def __post_init__(self):
        Scope(self.profile_id, self.project_id)
        if self.schema != CANDIDATE_SCHEMA:
            raise ValueError('unsupported candidate request schema')
        unique_ids(self.target_ids)
        identifier(self.snapshot_ref)
        sha256_value(self.snapshot_sha256)
        identifier(self.candidate_ref)
        sha256_value(self.candidate_sha256)
        unique_ids(self.evidence_refs)
        if self.intent not in ('preference', 'behavior_fix', 'new_capability'):
            raise ValueError('invalid candidate intent')
        _bounded_texts(self.conditions)
        identifier(self.scenario_id)
        identifier(self.idempotency_key)

    @property
    def scope(self) -> Scope:
        return Scope(self.profile_id, self.project_id)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> CandidateRequest:
        fields = {
            'schema', 'profile_id', 'project_id', 'target_ids', 'snapshot_ref',
            'snapshot_sha256', 'candidate_ref', 'candidate_sha256', 'evidence_refs',
            'intent', 'conditions', 'scenario_id', 'idempotency_key',
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('candidate request contains unknown or missing fields')
        if any(not isinstance(value[name], list) for name in ('target_ids', 'evidence_refs', 'conditions')):
            raise ValueError('candidate request selections must be arrays')
        try:
            return cls(**{**value, 'target_ids': tuple(value['target_ids']),
                          'evidence_refs': tuple(value['evidence_refs']),
                          'conditions': tuple(value['conditions'])})
        except TypeError:
            raise ValueError('invalid candidate request') from None


@dataclass(frozen=True)
class JobRef:
    profile_id: str
    project_id: str
    job_id: str

    def __post_init__(self):
        Scope(self.profile_id, self.project_id)
        identifier(self.job_id)

    @property
    def scope(self) -> Scope:
        return Scope(self.profile_id, self.project_id)


@dataclass(frozen=True)
class JobView:
    job: JobRef
    status: JobStatus
    reason_code: str | None = None
    candidate_id: str | None = None
    question_id: str | None = None
    comparison_ref: str | None = None

    def __post_init__(self):
        if not isinstance(self.job, JobRef) or self.status not in JOB_STATUSES:
            raise ValueError('invalid evolution job view')
        for value in (self.reason_code, self.candidate_id, self.question_id, self.comparison_ref):
            _optional_id(value)
        if (self.status == 'needs_input') != (self.question_id is not None):
            raise ValueError('only a needs_input job has an open question')


@dataclass(frozen=True)
class JobEvent:
    job: JobRef
    seq: int
    status: JobStatus
    reason_code: str | None = None

    def __post_init__(self):
        if (not isinstance(self.job, JobRef) or type(self.seq) is not int or self.seq <= 0
                or self.status not in JOB_STATUSES):
            raise ValueError('invalid evolution event')
        _optional_id(self.reason_code)


@dataclass(frozen=True)
class EventPage:
    events: tuple[JobEvent, ...]
    next_after: int

    def __post_init__(self):
        if (not isinstance(self.events, tuple) or len(self.events) > MAX_EVENT_PAGE
                or any(not isinstance(event, JobEvent) for event in self.events)):
            raise ValueError('invalid evolution event page')
        event_cursor(self.next_after)
        if self.events:
            jobs = {event.job for event in self.events}
            seqs = tuple(event.seq for event in self.events)
            if len(jobs) != 1 or seqs != tuple(sorted(set(seqs))) or self.next_after != seqs[-1]:
                raise ValueError('event page is not monotonic')


class ArtifactPort(Protocol):
    def read_artifact(self, scope: Scope, ref: str, *, kind: str) -> ArtifactView: ...

    def register_candidate(
        self, scope: Scope, snapshot_ref: str, files: Mapping[str, str],
    ) -> ArtifactRef: ...


class EvolutionQueuePort(Protocol):
    def submit_candidate(self, request: CandidateRequest) -> JobRef: ...

    def read_job(self, job: JobRef) -> JobView: ...

    def read_events(self, job: JobRef, *, after: int) -> EventPage: ...


# Compatibility spelling used by the first D implementation.  New code should
# name the full public queue protocol.
CandidateQueue = EvolutionQueuePort


class PortRejected(ValueError):
    """A scoped evolution operation rejected the request or is unavailable."""

    def __init__(self, reason_code: str):
        self.reason_code = identifier(reason_code)
        super().__init__(reason_code)
