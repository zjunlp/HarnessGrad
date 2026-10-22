"""DGM's staged small -> medium -> full evaluation schedule."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class Stage:
    name: str
    budget: int
    full: bool = False

    def __post_init__(self) -> None:
        if self.budget < 0:
            raise ValueError("stage budget cannot be negative")


@dataclass(frozen=True)
class Evaluation:
    node_id: str
    stage: str
    submitted: int
    resolved: int
    score: float | None = None
    required_full_population: int | None = None
    full_population: int | None = None

    def __post_init__(self) -> None:
        if self.submitted < 0 or self.resolved < 0 or self.resolved > self.submitted:
            raise ValueError("evaluation counts must satisfy 0 <= resolved <= submitted")
        if self.required_full_population is not None and self.required_full_population < 0:
            raise ValueError("required full population cannot be negative")
        if self.full_population is not None and self.full_population < 0:
            raise ValueError("full population count cannot be negative")
        if (self.required_full_population is not None and self.full_population is not None
                and self.full_population > self.required_full_population):
            raise ValueError("full population cannot exceed the required population")
        if self.score is not None and (not math.isfinite(self.score) or not 0 <= self.score <= 1):
            raise ValueError("evaluation score must be finite and in [0, 1]")

    @property
    def completion(self) -> float:
        return self.submitted and self.resolved / self.submitted or 0.0


@dataclass(frozen=True)
class MediumCandidate:
    """The current child being considered for escalation."""

    node_id: str
    submitted: int
    resolved: int
    score: float

    def __post_init__(self) -> None:
        if self.submitted < 0 or self.resolved < 0 or self.resolved > self.submitted:
            raise ValueError("candidate counts must satisfy 0 <= resolved <= submitted")
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("candidate score must be finite and in [0, 1]")


@dataclass(frozen=True)
class ArchiveFullResult:
    """A historical archive score that may contribute to DGM's threshold."""

    node_id: str
    score: float
    submitted: int
    required_full_population: int

    def __post_init__(self) -> None:
        if (self.required_full_population <= 0 or self.submitted < 0
                or self.submitted > self.required_full_population):
            raise ValueError("archive counts must satisfy 0 <= submitted <= population")
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("archive score must be finite and in [0, 1]")

    @property
    def full_enough(self) -> bool:
        """Whether this result meets the source's fixed 90% full-eval gate."""
        return self.is_full_enough(0.9)

    def is_full_enough(self, fraction: float) -> bool:
        return self.submitted >= self.required_full_population * fraction


@dataclass(frozen=True)
class DGMStagedScheduler:
    """Immutable scheduler matching DGM's additional-evaluation gate."""

    stages: tuple[Stage, ...] = (Stage("small", 0), Stage("medium", 0),
                                  Stage("full", 0, True))
    escalation_threshold: float = 0.4
    full_coverage_fraction: float = 0.9
    baseline_score: float | None = None
    medium_to_full_threshold: float | None = None
    archive_full_results: tuple[ArchiveFullResult, ...] = ()

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("at least one stage is required")
        if (not math.isfinite(self.escalation_threshold)
                or not 0 <= self.escalation_threshold <= 1):
            raise ValueError("escalation threshold must be finite and in [0, 1]")
        if (not math.isfinite(self.full_coverage_fraction)
                or not 0 < self.full_coverage_fraction <= 1):
            raise ValueError("full coverage fraction must be finite and in (0, 1]")
        if self.baseline_score is not None and (
                not math.isfinite(self.baseline_score) or not 0 <= self.baseline_score <= 1):
            raise ValueError("baseline score must be finite and in [0, 1]")
        if self.medium_to_full_threshold is not None and (
                not math.isfinite(self.medium_to_full_threshold)
                or not 0 <= self.medium_to_full_threshold <= 1):
            raise ValueError("medium-to-full threshold must be finite and in [0, 1]")

    def next_stage(self, evaluations: Iterable[Evaluation], node_id: str) -> Stage | None:
        done = [e for e in evaluations if e.node_id == node_id]
        if not done:
            return self.stages[0]
        current = max((self._index(e.stage) for e in done), default=-1)
        latest = max(done, key=lambda e: self._index(e.stage))
        # small->medium uses the small stage's task completion gate. medium->full
        # evaluates the current candidate against a threshold from historical
        # full-enough archive results. Candidate coverage is not archive eligibility.
        if current == 1:
            candidate = max((e for e in done if e.stage == "medium"),
                            key=lambda e: e.submitted, default=None)
            return (self.stages[current + 1]
                    if candidate and candidate.score is not None
                    and self.medium_reaches_full_threshold(
                        MediumCandidate(candidate.node_id, candidate.submitted,
                                        candidate.resolved, float(candidate.score)))
                    else None)
        if latest.completion < self.escalation_threshold:
            return None
        return self.stages[current + 1] if current + 1 < len(self.stages) else None

    def budget_plan(self, evaluations: Iterable[Evaluation], node_id: str) -> dict:
        nxt = self.next_stage(evaluations, node_id)
        return {"node_id": node_id, "next_stage": nxt.name if nxt else None,
                "stage_index": self._index(nxt.name) if nxt else None,
                "escalation_threshold": self.escalation_threshold,
                "score_provenance": "study_only_until_full"}

    def consume(self, evaluations: Iterable[Evaluation], node_id: str,
                available: int) -> tuple[Stage | None, int, dict]:
        """Reserve only the next stage's budget; never charge full eval twice."""
        if available < 0:
            raise ValueError("available budget cannot be negative")
        nxt = self.next_stage(evaluations, node_id)
        if nxt is None:
            return None, 0, {"node_id": node_id, "budget_before": available,
                             "budget_after": available, "consumed": 0}
        consumed = min(available, max(0, nxt.budget))
        return nxt, consumed, {"node_id": node_id, "stage": nxt.name,
                               "budget_before": available,
                               "budget_after": available - consumed,
                               "consumed": consumed,
                               "truncated": consumed < nxt.budget}

    def medium_reaches_full_threshold(self, candidate: MediumCandidate) -> bool:
        if self.medium_to_full_threshold is not None:
            return candidate.score >= self.medium_to_full_threshold
        if self.baseline_score is None or not math.isfinite(self.baseline_score):
            return False
        threshold = self.full_eval_threshold(
            baseline=self.baseline_score,
            archive_results=self.archive_full_results,
            coverage_fraction=self.full_coverage_fraction,
        )
        # Upstream self_improve_step.py escalates at `resolved >= n * threshold`;
        # the equivalent normalized comparison is inclusive (`score >= threshold`).
        return candidate.score >= threshold

    def full_eval_threshold(self, *, baseline: float,
                            archive_results: Iterable[ArchiveFullResult],
                            coverage_fraction: float | None = None) -> float:
        if not math.isfinite(baseline) or not 0 <= baseline <= 1:
            raise ValueError("baseline score must be finite and in [0, 1]")
        fraction = self.full_coverage_fraction if coverage_fraction is None else coverage_fraction
        if not math.isfinite(fraction) or not 0 < fraction <= 1:
            raise ValueError("full coverage fraction must be finite and in (0, 1]")
        values = [float(baseline)]
        for result in archive_results:
            if not isinstance(result, ArchiveFullResult):
                raise ValueError("archive threshold requires typed ArchiveFullResult entries")
            if result.is_full_enough(fraction):
                values.append(result.score)
        values.sort(reverse=True)
        return max(values[1] if len(values) > 1 else values[0], 0.4)

    def _index(self, name: str) -> int:
        for i, stage in enumerate(self.stages):
            if stage.name == name:
                return i
        raise ValueError(f"unknown DGM evaluation stage {name!r}")
