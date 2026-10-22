"""HyperAgents staged-eval bookkeeping without score rescaling."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HyperEvaluation:
    node_id: str
    staged_fraction: float
    run_full_eval: bool = False
    score: float | None = None


@dataclass(frozen=True)
class HyperStagedScheduler:
    """The source's staged fraction is recorded, never applied to raw scores."""

    full_threshold: float = 0.4

    def next_stage(self, evaluation: HyperEvaluation | None) -> str:
        if evaluation is None:
            return "staged"
        if evaluation.run_full_eval:
            return "complete"
        if evaluation.score is None:
            return "staged_complete"
        return "full" if evaluation.score >= self.full_threshold else "staged_complete"

    def record(self, evaluation: HyperEvaluation | None) -> dict:
        return {
            "stage": self.next_stage(evaluation),
            "staged_fraction": evaluation.staged_fraction if evaluation else None,
            "run_full_eval": bool(evaluation and evaluation.run_full_eval),
            "threshold": self.full_threshold,
            "escalation_eligible": bool(evaluation and evaluation.score is not None
                                          and evaluation.score >= self.full_threshold),
            "score_rescaling": "none",
            "score_provenance": "raw_domain_score",
        }

    def consume(self, evaluation: HyperEvaluation | None, available: int) -> tuple[str, int, dict]:
        if available < 0:
            raise ValueError("available budget cannot be negative")
        stage = self.next_stage(evaluation)
        # Staged fraction is a domain-specific fraction, not an API budget. The
        # scheduler therefore consumes the caller's unit budget once per phase.
        requested = 1
        consumed = min(available, requested)
        return stage, consumed, {"budget_before": available,
                                 "budget_after": available - consumed,
                                 "consumed": consumed,
                                 "score_rescaling": "none"}
