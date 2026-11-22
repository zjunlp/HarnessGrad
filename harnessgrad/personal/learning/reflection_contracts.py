"""D2 contracts and temporary projections of the v1 C ports."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Literal, Protocol

from harnessgrad.personal.contracts import identifier
from harnessgrad.personal.evidence import MAX_SELECTIONS
from harnessgrad.personal.learning.contracts import LearningScope

CANDIDATE_SCHEMA = 'harnessgrad.personal.evolution.candidate-request.v1'
ExperienceKind = Literal['memory', 'rule', 'skill']
LearningStatus = Literal['candidate_ready', 'no_change', 'deferred']


def sha256_value(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('expected a SHA-256 digest')


def unique_ids(values: tuple[str, ...], *, limit: int = MAX_SELECTIONS) -> None:
    if not isinstance(values, tuple) or not 1 <= len(values) <= limit:
        raise ValueError('expected a bounded, nonempty selection')
    for value in values:
        identifier(value)
    if len(set(values)) != len(values):
        raise ValueError('duplicate selection')


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


@dataclass(frozen=True)
class ArtifactView(ArtifactRef):
    payload: Mapping
    truncated: bool = False


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
    intent: str
    conditions: tuple[str, ...]
    scenario_id: str
    idempotency_key: str
    schema: str = CANDIDATE_SCHEMA

    @classmethod
    def from_dict(cls, value: dict) -> CandidateRequest:
        return cls(**{**value, 'target_ids': tuple(value['target_ids']),
                      'evidence_refs': tuple(value['evidence_refs']), 'conditions': tuple(value['conditions'])})


@dataclass(frozen=True)
class JobRef:
    profile_id: str
    project_id: str
    job_id: str


class ArtifactPort(Protocol):
    def read_artifact(self, scope: LearningScope, ref: str, *, kind: str) -> ArtifactView: ...

    def register_candidate(
        self, scope: LearningScope, snapshot_ref: str, files: Mapping[str, str],
    ) -> ArtifactRef: ...


class CandidateQueue(Protocol):
    def submit_candidate(self, request: CandidateRequest) -> JobRef: ...


class PortRejected(ValueError):
    """A scoped C operation rejected the request or is unavailable."""

    def __init__(self, reason_code: str):
        self.reason_code = identifier(reason_code)
        super().__init__(reason_code)


@dataclass(frozen=True)
class ReflectionInput:
    scope: LearningScope
    evidence_refs: tuple[str, ...]
    snapshot: SnapshotRef
    scenario_id: str
    constraints: tuple[str, ...] = ()

    def __post_init__(self):
        unique_ids(self.evidence_refs)
        object.__setattr__(self, 'evidence_refs', tuple(sorted(self.evidence_refs)))
        identifier(self.scenario_id)
        if (not isinstance(self.constraints, tuple) or len(self.constraints) > 32
                or any(not isinstance(item, str) or not item.strip() or len(item.encode('utf-8')) > 1024
                       for item in self.constraints)):
            raise ValueError('reflection constraints must be bounded, nonempty text')


@dataclass(frozen=True)
class ExistingExperience:
    scope: LearningScope
    snapshot_sha256: str
    topic_id: str
    kind: ExperienceKind
    path: str
    text: str
    version_ref: str


@dataclass(frozen=True)
class Experience:
    topic_id: str
    kind: ExperienceKind
    path: str
    criterion: str
    prior_version_ref: str | None
    text: str | None = None


@dataclass(frozen=True)
class LearningDecision:
    status: LearningStatus
    candidate: CandidateRequest | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class ReflectionReceipt:
    decision: LearningDecision
    experience: Experience | None = None
    job: JobRef | None = None
