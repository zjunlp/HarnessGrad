"""Pure, offline RRSI fidelity components.

This module contains the part of RRSI that can be exercised without a model,
git worktree, or benchmark runner: role-specific prompts, noise calibration,
and Algorithm 2's candidate pool selection.  The selection function always
returns a next parent.  When every proposal is gated or inadmissible that
parent is the incumbent, which is the ``H_{t+1}=H_t`` branch of the original
loop.

A caller supplies measurements made by its evaluator.  This module never
looks at task data (and has no held-out/evolve dataset handle), so it is safe to
use in deterministic unit tests and in a study-only replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import statistics
from typing import Any, Callable, Mapping, Sequence


def _real(value: Any, name: str) -> float:
    """Return a finite real number, rejecting bool and non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _score(value: Any, name: str) -> float:
    value = _real(value, name)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return value


def _cost(value: Any, name: str, *, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    value = _real(value, name)
    if value < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class TrainContext:
    """Caller-owned evidence explicitly declared to be evolve/train data."""

    text: str
    source: str = "evolve/train"
    trusted_runtime: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("TrainContext.text must be a string")
        if self.source != "evolve/train" or not self.trusted_runtime:
            raise ValueError("TrainContext must be trusted evolve/train evidence")


@dataclass(frozen=True)
class StudyTaskSet:
    """Runtime-attested task IDs allowed into study evaluation."""

    ids: tuple[str, ...]
    source: str = "evolve/train"
    trusted_runtime: bool = False

    def __post_init__(self) -> None:
        if self.source != "evolve/train" or not self.trusted_runtime:
            raise ValueError("StudyTaskSet must be trusted evolve/train data")
        ids = tuple(self.ids)
        if (not ids or any(not isinstance(x, str) or not x for x in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("StudyTaskSet.ids must be unique non-empty IDs")
        object.__setattr__(self, "ids", ids)


# Exact source prompt hashes at the pinned upstream commit. ROLE_PROMPTS below
# are adapted compact templates, not verbatim copies.
SOURCE_ROLE_PROMPT_HASHES: Mapping[str, Mapping[str, str]] = {
    "digester": {"path": "rrsi/digester.py:SYSTEM_TMPL", "sha256": "24c9ceb093eee4ed22eae971d65001f075781543de77b45cdeebd476907dd0b3"},
    "analyst": {"path": "rrsi/analyst.py:SYSTEM_TMPL", "sha256": "11287c002c2d09d9b48cd6f22c290ae9073c33a0dabe01db12a5da76f12c5703"},
    "proposer": {"path": "rrsi/propose.py:SYSTEM_TMPL", "sha256": "cb31c5acc8f70eb0454e9e35ed146aa96f458f8d309934846769b20890f1fe10"},
    "critic": {"path": "rrsi/critic.py:SYSTEM_TMPL", "sha256": "8818c7e1d7b082190cc9abd72b368160d1818a3ee9587badda7709af515b94ed"},
}

# Action names retained from the pinned templates. The prompt bodies below are
# compact adaptations, but a future runtime must keep these role boundaries and
# tool protocols instead of routing every role through a generic editor.
ROLE_ACTION_PROTOCOLS: Mapping[str, tuple[str, ...]] = {
    "digester": ("read_file", "glob", "grep", "bash", "return"),
    "analyst": ("digest_many", "report"),
    "proposer": ("list_files", "read_file", "list_traces", "read_trace",
                 "edit_file", "write_file", "done"),
    "critic": ("verdict",),
}


# RRSI's role separation is intentional.  In particular, a proposer must not
# be substituted for an analyst/digester, and the critic is a pre-evaluation
# reviewer rather than an editor.  Keep train/evolve evidence in all role
# contexts; held-out evidence is never part of a role prompt.
ROLE_PROMPTS: Mapping[str, str] = {
    "digester": """You are RRSI's read-only trajectory digester. Inspect exactly one
evolve-set trajectory with read-only tools, then return one strict JSON action:
{\"action\":\"return\",\"digest\":{...}}. The digest schema is
{task_id,lens,blocker|wanted, narrative|why_couldnt, evidence:[{where,quote}],
verifier_evidence, needed_instead}; use lens=failure, capability_gap, or success.
Every claim needs an exact step reference. You may read/grep the rendered trace;
you may not edit files, infer answers, or inspect held-out tasks.""",
    "analyst": """You are RRSI's batch analyst. Dispatch read-only digesters with
{\"action\":\"digest_many\",\"requests\":[{task_id,lens,questions}]} and finish
with one strict JSON {\"action\":\"report\",\"failure_modes\":[],
\"capability_gaps\":[],\"success_habits\":[]}. Merge repeated mechanisms,
rank failure modes by impact, preserve evidence references, and keep descriptions
task-agnostic. This is diagnosis for a proposer: do not prescribe a patch and
do not read or select on held-out results.""",
    "proposer": """You are RRSI's proposer. Draft one candidate harness variant from
the incumbent, analyst report, edit history, annealed budget b_t, exploration
reservation E_t, and prune set B_t. Return exactly one structured JSON action
per turn; finish with {\"action\":\"done\",\"summary\":...,\"edits\":[
{id,component,hypothesis,targets_mode,why_not_lower_lever,trigger_condition,
predicted_affected,retroactive_check,regression_risk}]}. Respect the edit budget,
component tags, and reserved slot. Changes must be general and task-agnostic.
Work only from evolve/train evidence; never use held-out scores, answers, or
task-specific constants.""",
    "critic": """You are RRSI's pre-evaluation critic. Review the candidate diff
before any study evaluation and return strict JSON only:
{\"verdict\":\"accept\"|\"reject\",\"reasons\":[],\"risk_notes\":[]}.
Reject leakage/task specialization, grader gaming, undeclared bundling, runtime
memory leakage, degenerate edits, and unbounded work. Review intent and content;
do not edit, execute, or evaluate the candidate. Held-out data is inaccessible
to this review.""",
}


def role_prompt(role: str, context: TrainContext | None = None) -> str:
    """Render one RRSI role prompt with an optional train-side context.

    ``context`` is caller-supplied evidence (for example a digest report).  The
    The typed context is a caller-owned/trusted-runtime boundary. It labels the
    provenance but cannot inspect arbitrary text and prove that it contains no
    exam value; the runtime must construct it from study-side evidence.
    """
    try:
        preamble = ROLE_PROMPTS[role]
    except KeyError as exc:
        raise ValueError(f"unknown RRSI role: {role!r}") from exc
    if context is not None and not isinstance(context, TrainContext):
        raise TypeError("role_prompt requires typed TrainContext evidence")
    body = context.text.strip() if context is not None else ""
    return (preamble + "\n\nSOURCE BOUNDARY: evolve/train evidence only; "
            "held-out evidence is inaccessible."
            + (f"\n\n{body}" if body else ""))


@dataclass(frozen=True)
class SelectionConfig:
    """Algorithm 2 weights and guards (RRSI published defaults)."""

    beta0: float = 0.10
    beta1: float = 40.0
    w_s: float = 100.0
    w_c: float = 15.0
    w_n: float = 0.5

    def __post_init__(self) -> None:
        for name in ("beta0", "beta1", "w_s", "w_c", "w_n"):
            value = _real(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class Calibration:
    """A noise-band estimate and its provenance."""

    delta: float
    z: float
    source: str
    sd_null: float
    n_observations: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta": self.delta,
            "z": self.z,
            "source": self.source,
            "sd_null": self.sd_null,
            "n_observations": self.n_observations,
        }


def calibrate_delta(
    repeated_scores: Sequence[float] | None = None,
    *,
    score_std: float | None = None,
    n_trials: int | None = None,
    z: float = 2.0,
) -> Calibration:
    """Estimate ``delta = z * sd(null Delta S)``.

    With at least two independent evaluations of the unchanged harness, RRSI's
    estimator is sample standard deviation times ``sqrt(2)``.  A platform that
    has one multi-trial measurement may provide its run-level ``score_std``;
    that is ``sd(S)`` and therefore uses the same ``sqrt(2)`` null-difference
    factor.  A one-trial measurement has no calibration signal and is rejected
    explicitly rather than silently setting the band to zero.
    """
    z = _real(z, "z")
    if z <= 0:
        raise ValueError("z must be a positive finite number")
    scores = [_score(x, "repeated score") for x in (repeated_scores or [])]
    if len(scores) >= 2:
        sd = statistics.stdev(scores) * math.sqrt(2.0)
        return Calibration(z * sd, float(z), "repeated_base_evaluations", sd, len(scores))
    if n_trials is not None:
        n_trials = _count(n_trials, "n_trials")
    sd = None
    if score_std is not None:
        sd = _real(score_std, "score_std")
        if sd < 0:
            raise ValueError("score_std must be finite and non-negative")
    if sd is not None and n_trials is not None and n_trials > 1:
        null_sd = sd * math.sqrt(2.0)
        return Calibration(z * null_sd, float(z), "platform_trial_spread", null_sd, int(n_trials))
    raise ValueError(
        "calibration needs >=2 repeated base scores or score_std with n_trials>1"
    )


def relative_cost_change(candidate_cost: float | None, incumbent_cost: float | None) -> tuple[float, bool]:
    """Return RRSI's relative ``Delta C`` and whether token accounting exists."""
    candidate_cost = _cost(candidate_cost, "candidate_cost")
    incumbent_cost = _cost(incumbent_cost, "incumbent_cost")
    # A measured zero candidate cost is valid. A zero incumbent has no defined
    # relative denominator, so cost accounting is unavailable in that case.
    if candidate_cost is None or incumbent_cost is None or incumbent_cost == 0:
        return 0.0, False
    return (candidate_cost - incumbent_cost) / incumbent_cost, True


def cost_rule(
    delta_s: float, delta_c: float, novelty: int, delta: float,
    config: SelectionConfig = SelectionConfig(),
) -> tuple[bool, str]:
    """RRSI's two-branch cost rule (novelty appears only inside the band)."""
    delta = _real(delta, "delta")
    if delta < 0:
        raise ValueError("delta must be non-negative")
    delta_s = _real(delta_s, "delta_s")
    delta_c = _real(delta_c, "delta_c")
    _count(novelty, "novelty")
    if not isinstance(config, SelectionConfig):
        raise ValueError("config must be a SelectionConfig")
    if delta_s > delta:
        budget = config.beta0 + config.beta1 * delta_s
        return delta_c <= budget, "gain_above_band"
    shaped = config.w_s * delta_s - config.w_c * delta_c + config.w_n * novelty
    return shaped > 0, "within_band"


@dataclass
class Candidate:
    """A measured or gate-rejected RRSI candidate.

    ``gate_failure`` records critic/smoke/evaluator failures. Such a candidate
    can never be selected, regardless of any score accidentally attached to it.
    """

    variant: str
    score: float | None = None
    cost: float | None = None
    components: tuple[str, ...] = ()
    gate_failure: str | None = None
    evaluated: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.variant, str) or not self.variant:
            raise ValueError("candidate variant must be a non-empty string")
        if self.score is not None:
            _score(self.score, f"candidate {self.variant} score")
        _cost(self.cost, f"candidate {self.variant} cost")
        if not isinstance(self.evaluated, bool):
            raise ValueError("candidate evaluated must be boolean")


@dataclass(frozen=True)
class Proposal:
    """An unevaluated artifact produced from the same incumbent parent.

    The artifact is opaque to this module.  Keeping it separate from
    :class:`Candidate` prevents a proposer-side self-reported score from
    bypassing the critic/evaluator stages in the offline round simulator.
    """

    variant: str
    artifact: Any = None
    components: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.variant, str) or not self.variant:
            raise ValueError("proposal variant must be a non-empty string")


@dataclass(frozen=True)
class CandidateDecision:
    variant: str
    admissible: bool
    reason: str
    score: float | None = None
    delta_s: float | None = None
    delta_c: float | None = None
    novelty: int = 0
    stage: str = "gate"


@dataclass(frozen=True)
class SelectionResult:
    """A complete, replayable round decision."""

    parent_id: str
    incumbent_retained: bool
    winner_id: str | None
    decisions: tuple[CandidateDecision, ...]
    floor: float
    s_star: float

    @property
    def accepted_ids(self) -> tuple[str, ...]:
        return tuple(d.variant for d in self.decisions if d.admissible)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_id": self.parent_id,
            "incumbent_retained": self.incumbent_retained,
            "winner_id": self.winner_id,
            "floor": self.floor,
            "S_star": self.s_star,
            "decisions": [d.__dict__ for d in self.decisions],
        }


@dataclass(frozen=True)
class SimulationResult:
    """Recorded output of one candidate -> critic -> study-eval -> selection round."""

    selection: SelectionResult
    candidates: tuple[Candidate, ...]
    critic_verdicts: tuple[dict[str, Any], ...]
    evaluations: tuple[dict[str, Any], ...]
    study_task_ids: tuple[str, ...]
    trials: int

    @property
    def next_parent(self) -> str:
        """The only parent identity a deployment layer may consume."""
        return self.selection.parent_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection": self.selection.to_dict(),
            "candidates": [
                {
                    "variant": c.variant,
                    "score": c.score,
                    "cost": c.cost,
                    "gate_failure": c.gate_failure,
                    "evaluated": c.evaluated,
                    "metadata": c.metadata,
                }
                for c in self.candidates
            ],
            "critic_verdicts": list(self.critic_verdicts),
            "evaluations": list(self.evaluations),
            "study_task_ids": list(self.study_task_ids),
            "trials": self.trials,
            "next_parent": self.next_parent,
        }


def judge_candidate(
    candidate: Candidate,
    *,
    incumbent_score: float,
    incumbent_cost: float | None,
    s_star: float,
    delta: float,
    incumbent_counts: Mapping[str, int] | None = None,
    config: SelectionConfig = SelectionConfig(),
) -> CandidateDecision:
    """Judge one candidate, before pool argmax is applied."""
    incumbent_score = _score(incumbent_score, "incumbent_score")
    s_star = _score(s_star, "s_star")
    delta = _real(delta, "delta")
    if delta < 0:
        raise ValueError("delta must be non-negative")
    _cost(incumbent_cost, "incumbent_cost")
    if not isinstance(config, SelectionConfig):
        raise ValueError("config must be a SelectionConfig")
    if candidate.gate_failure:
        return CandidateDecision(candidate.variant, False,
                                 f"gate failure: {candidate.gate_failure}",
                                 candidate.score, stage="gate")
    if not candidate.evaluated or candidate.score is None:
        return CandidateDecision(candidate.variant, False, "not evaluated",
                                 candidate.score, stage="evaluation")
    d_s = float(candidate.score) - float(incumbent_score)
    d_c, cost_available = relative_cost_change(candidate.cost, incumbent_cost)
    counts = incumbent_counts or {}
    novelty = sum(
        1 for component in set(candidate.components)
        if component in {"client_tool", "skill", "memory", "subagent"}
        and counts.get(component, 0) == 0
    )
    floor = float(s_star) - float(delta)
    if candidate.score < floor:
        return CandidateDecision(candidate.variant, False,
                                 "below noise-adjusted floor", candidate.score,
                                 d_s, d_c, novelty, "floor")
    if not cost_available:
        # The upstream evaluator substitutes dC=0, but a platform port must not
        # report a cost verdict when token accounting was absent.
        return CandidateDecision(candidate.variant, False,
                                 "cost accounting unavailable", candidate.score,
                                 d_s, d_c, novelty, "cost_unavailable")
    ok, branch = cost_rule(d_s, d_c, novelty, delta, config)
    return CandidateDecision(candidate.variant, ok,
                             branch if ok else f"cost rule failed ({branch})",
                             candidate.score, d_s, d_c, novelty,
                             "admissible" if ok else "cost")


def select_candidates(
    candidates: Sequence[Candidate],
    *,
    incumbent_id: str,
    incumbent_score: float,
    incumbent_cost: float | None,
    s_star: float,
    delta: float,
    incumbent_counts: Mapping[str, int] | None = None,
    config: SelectionConfig = SelectionConfig(),
) -> SelectionResult:
    """Apply RRSI selection to an ``m``-candidate pool.

    All candidates are judged independently; the maximum score is taken only
    over admissible candidates. Ties are stable in input order. If no candidate
    is admissible, ``parent_id`` is exactly ``incumbent_id`` and no rejected
    candidate can become the next parent.
    """
    candidates = tuple(candidates)
    if not isinstance(incumbent_id, str) or not incumbent_id:
        raise ValueError("incumbent_id must be a non-empty string")
    _score(incumbent_score, "incumbent_score")
    _score(s_star, "s_star")
    delta = _real(delta, "delta")
    if delta < 0:
        raise ValueError("delta must be non-negative")
    _cost(incumbent_cost, "incumbent_cost")
    variants = [candidate.variant for candidate in candidates]
    if len(set(variants)) != len(variants):
        raise ValueError("candidate variant IDs must be unique")
    if incumbent_id in variants:
        raise ValueError("candidate variant ID collides with incumbent_id")
    decisions = tuple(
        judge_candidate(c, incumbent_score=incumbent_score,
                        incumbent_cost=incumbent_cost, s_star=s_star,
                        delta=delta, incumbent_counts=incumbent_counts,
                        config=config)
        for c in candidates
    )
    admissible = [
        (c, d) for c, d in zip(candidates, decisions) if d.admissible
    ]
    if not admissible:
        return SelectionResult(incumbent_id, True, None, decisions,
                               float(s_star) - float(delta), float(s_star))
    winner, _ = max(admissible, key=lambda pair: float(pair[1].score))
    # Validate the invariant explicitly: a gate-failed candidate can never be
    # promoted by score alone. This is an input error, not an assert-based guard.
    if winner.gate_failure is not None:
        raise ValueError("selection invariant violated: gated candidate selected")
    return SelectionResult(winner.variant, False, winner.variant, decisions,
                           float(s_star) - float(delta), float(s_star))


def simulate_round(
    proposals: Sequence[Proposal],
    *,
    incumbent_id: str,
    incumbent_score: float,
    incumbent_cost: float | None,
    s_star: float,
    delta: float,
    study_task_ids: StudyTaskSet,
    trials: int,
    critic: Callable[[Proposal], Mapping[str, Any]],
    evaluate_many: Callable[[Sequence[Proposal], Sequence[str], int],
                            Mapping[str, Mapping[str, Any]]],
    incumbent_counts: Mapping[str, int] | None = None,
    config: SelectionConfig = SelectionConfig(),
) -> SimulationResult:
    """Run the native round ordering with mocked external boundaries.

    The callbacks model the two capabilities that the current platform does
    not yet expose: a pre-evaluation role critic and a trusted study-only
    ``evaluate_many``.  Critic-rejected proposals never reach the evaluator;
    the evaluator receives only the supplied study IDs and the resulting
    selection is the sole source of the next parent identity.
    """
    proposals = tuple(proposals)
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError("trials must be positive")
    if not isinstance(study_task_ids, StudyTaskSet):
        raise TypeError("simulate_round requires an attested StudyTaskSet")
    if not isinstance(incumbent_id, str) or not incumbent_id:
        raise ValueError("incumbent_id must be a non-empty string")
    ids = study_task_ids.ids
    variants = [p.variant for p in proposals]
    if len(set(variants)) != len(variants):
        raise ValueError("candidate variants must be unique")
    if incumbent_id in variants:
        raise ValueError("candidate variant ID collides with incumbent_id")

    verdicts: list[dict[str, Any]] = []
    live: list[Proposal] = []
    candidates: list[Candidate] = []
    for proposal in proposals:
        verdict = dict(critic(proposal) or {})
        verdict.setdefault("variant", proposal.variant)
        verdicts.append(verdict)
        if verdict.get("verdict") != "accept":
            candidates.append(Candidate(
                proposal.variant, components=proposal.components,
                gate_failure="critic_reject", evaluated=False,
                metadata={"critic": verdict},
            ))
            continue
        live.append(proposal)

    raw_evaluations = evaluate_many(live, ids, int(trials)) if live else {}
    if raw_evaluations is None:
        raw_evaluations = {}
    if not isinstance(raw_evaluations, Mapping):
        raise ValueError("evaluate_many must return a mapping by candidate ID")
    live_ids = {proposal.variant for proposal in live}
    extra_ids = set(raw_evaluations) - live_ids
    if extra_ids:
        raise ValueError(f"evaluate_many returned unknown candidate IDs: {sorted(extra_ids)}")
    evaluations: list[dict[str, Any]] = []
    for proposal in live:
        if proposal.variant not in raw_evaluations:
            raw = {"candidate_id": proposal.variant,
                   "measured": False, "missing": True}
        else:
            item = raw_evaluations[proposal.variant]
            if not isinstance(item, Mapping):
                raise ValueError(f"evaluation for {proposal.variant} must be a mapping")
            raw = dict(item)
        raw.setdefault("candidate_id", proposal.variant)
        if raw["candidate_id"] != proposal.variant:
            raise ValueError(f"evaluation candidate_id mismatch for {proposal.variant}")
        evaluations.append(raw)
        score = raw.get("score", raw.get("aggregate_score"))
        cost = raw.get("cost", raw.get("token_cost"))
        score_key = "score" if "score" in raw else "aggregate_score"
        cost_key = "cost" if "cost" in raw else "token_cost"
        if score is not None:
            _score(score, f"evaluation {proposal.variant} {score_key}")
        if cost is not None:
            _cost(cost, f"evaluation {proposal.variant} {cost_key}")
        valid_score = isinstance(score, (int, float)) and not isinstance(score, bool)
        measured = raw.get("measured", True)
        if not isinstance(measured, bool):
            raise ValueError(f"evaluation measured flag for {proposal.variant} must be boolean")
        gate_failure = raw.get("gate_failure")
        if gate_failure is not None and not isinstance(gate_failure, str):
            raise ValueError(f"evaluation gate_failure for {proposal.variant} must be text")
        if raw.get("missing") is True:
            measured = False
            gate_failure = gate_failure or "evaluation_missing"
        if not measured:
            valid_score = False
            gate_failure = gate_failure or "measurement_not_attested"
        candidates.append(Candidate(
            proposal.variant,
            score=float(score) if valid_score else None,
            cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
            components=proposal.components,
            gate_failure=gate_failure,
            evaluated=valid_score and measured,
            metadata={"evaluation": raw},
        ))

    # Keep proposal order in the result even though critic-gated candidates are
    # never sent to evaluate_many.
    by_variant = {candidate.variant: candidate for candidate in candidates}
    ordered_candidates = tuple(by_variant[p.variant] for p in proposals)
    selection = select_candidates(
        ordered_candidates,
        incumbent_id=incumbent_id,
        incumbent_score=incumbent_score,
        incumbent_cost=incumbent_cost,
        s_star=s_star,
        delta=delta,
        incumbent_counts=incumbent_counts,
        config=config,
    )
    return SimulationResult(
        selection=selection,
        candidates=ordered_candidates,
        critic_verdicts=tuple(verdicts),
        evaluations=tuple(evaluations),
        study_task_ids=ids,
        trials=int(trials),
    )
