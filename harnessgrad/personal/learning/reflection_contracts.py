"""D2 reflection contracts built on the public evolution boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from harnessgrad.personal.contracts import identifier
from harnessgrad.personal.evolution_contracts import (
    CANDIDATE_SCHEMA,
    ArtifactPort,
    ArtifactRef,
    ArtifactView,
    CandidateQueue,
    CandidateRequest,
    EvolutionQueuePort,
    JobRef,
    PortRejected,
    SnapshotRef,
    sha256_value,
    unique_ids,
)
from harnessgrad.personal.learning.contracts import LearningScope

ExperienceKind = Literal['memory', 'rule', 'skill']
LearningStatus = Literal['candidate_ready', 'no_change', 'deferred']


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
