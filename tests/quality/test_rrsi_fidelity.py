"""Offline fidelity checks for RRSI's pool selection and calibration rules."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "methods"))

from rrsi.fidelity import (  # noqa: E402
    Candidate,
    Proposal,
    SelectionConfig,
    ROLE_ACTION_PROTOCOLS,
    SOURCE_ROLE_PROMPT_HASHES,
    StudyTaskSet,
    TrainContext,
    calibrate_delta,
    relative_cost_change,
    role_prompt,
    simulate_round,
    select_candidates,
)


def test_multiple_candidates_selects_highest_admissible_score_only():
    result = select_candidates(
        [
            Candidate("A", score=0.96, cost=300, gate_failure="critic_reject"),
            Candidate("B", score=0.91, cost=130),
            Candidate("C", score=0.87, cost=101),
        ],
        incumbent_id="incumbent", incumbent_score=0.80, incumbent_cost=100,
        s_star=0.90, delta=0.02, config=SelectionConfig(),
    )
    assert result.parent_id == "B"
    assert result.winner_id == "B"
    assert result.accepted_ids == ("B",)
    assert result.decisions[0].admissible is False
    assert "gate failure" in result.decisions[0].reason
    assert result.decisions[2].stage == "floor"


def test_highest_score_can_fail_cost_rule_and_all_rejected_keeps_incumbent():
    # A clears the score floor and has the highest score, but the above-band
    # cost budget is 0.10 + 40*0.20 = 8.1, below its 9x relative cost increase.
    # B is below the floor. The no-admissible branch must retain the incumbent.
    result = select_candidates(
        [Candidate("A", score=0.95, cost=1000),
         Candidate("B", score=0.70, cost=100)],
        incumbent_id="incumbent", incumbent_score=0.75, incumbent_cost=100,
        s_star=0.90, delta=0.05,
    )
    assert result.decisions[0].admissible is False
    assert result.decisions[0].stage == "cost"
    assert result.decisions[1].stage == "floor"
    assert result.parent_id == "incumbent"
    assert result.incumbent_retained is True
    assert result.winner_id is None
    assert result.accepted_ids == ()


def test_calibration_uses_repeated_base_or_single_measurement_trial_spread():
    repeated = calibrate_delta([0.4, 0.6, 0.5], z=2)
    assert repeated.source == "repeated_base_evaluations"
    assert repeated.delta == pytest.approx(2 * math.sqrt(0.02))

    platform = calibrate_delta(score_std=0.094, n_trials=3, z=2)
    assert platform.source == "platform_trial_spread"
    assert platform.delta == pytest.approx(2 * math.sqrt(2) * 0.094)

    with pytest.raises(ValueError, match="calibration needs"):
        calibrate_delta(score_std=0.0, n_trials=1)


def test_cost_accounting_distinguishes_zero_from_missing_like_the_port_policy():
    # Upstream replaces either missing token count with dC=0. The fidelity core
    # deliberately marks that case inactive; a measured zero-token candidate is
    # still real accounting and yields dC=-1 against a positive incumbent.
    assert relative_cost_change(0, 100) == pytest.approx((-1.0, True))
    assert relative_cost_change(None, 100) == pytest.approx((0.0, False))


def test_role_prompts_are_distinct_and_require_declared_train_context():
    prompts = {role: role_prompt(role, TrainContext("evolve score report", trusted_runtime=True))
               for role in ("digester", "analyst", "proposer", "critic")}
    assert len(set(prompts.values())) == 4
    assert all("held-out evidence is inaccessible" in p for p in prompts.values())
    assert "read-only" in prompts["digester"]
    assert "batch analyst" in prompts["analyst"]
    assert "proposer" in prompts["proposer"]
    assert "pre-evaluation critic" in prompts["critic"]
    assert all("evolve score report" in p for p in prompts.values())
    assert set(SOURCE_ROLE_PROMPT_HASHES) == {"digester", "analyst", "proposer", "critic"}
    assert all(len(item["sha256"]) == 64 for item in SOURCE_ROLE_PROMPT_HASHES.values())
    assert ROLE_ACTION_PROTOCOLS["digester"][-1] == "return"
    assert ROLE_ACTION_PROTOCOLS["analyst"] == ("digest_many", "report")
    assert ROLE_ACTION_PROTOCOLS["critic"] == ("verdict",)


def test_round_simulator_critic_filters_before_study_evaluate_and_selects_parent():
    proposals = [Proposal("leak", artifact="bad"), Proposal("expensive", artifact="x"),
                 Proposal("cheap", artifact="y")]
    critic_calls = []
    eval_calls = []

    def critic(proposal):
        critic_calls.append(proposal.variant)
        return {"verdict": "reject" if proposal.variant == "leak" else "accept"}

    def evaluate_many(live, task_ids, trials):
        eval_calls.append(([p.variant for p in live], tuple(task_ids), trials))
        return {
            "expensive": {"aggregate_score": 0.99, "token_cost": 2000},
            "cheap": {"aggregate_score": 0.93, "token_cost": 101},
        }

    result = simulate_round(
        proposals, incumbent_id="incumbent", incumbent_score=0.80,
        incumbent_cost=100, s_star=0.90, delta=0.02,
        study_task_ids=StudyTaskSet(("study-a", "study-b"), trusted_runtime=True), trials=2,
        critic=critic, evaluate_many=evaluate_many,
    )
    assert critic_calls == ["leak", "expensive", "cheap"]
    assert eval_calls == [(["expensive", "cheap"], ("study-a", "study-b"), 2)]
    assert result.next_parent == "cheap"
    assert result.selection.winner_id == "cheap"
    assert result.candidates[0].gate_failure == "critic_reject"
    assert result.candidates[1].evaluated is True
    assert result.candidates[1].score == pytest.approx(0.99)
    assert result.candidates[1].metadata["evaluation"]["token_cost"] == 2000
    assert all("heldout" not in task_id for task_id in result.study_task_ids)


def test_round_simulator_keeps_incumbent_when_every_candidate_is_rejected():
    proposals = [Proposal("A"), Proposal("B")]
    eval_calls = []

    def evaluate_many(*args):
        eval_calls.append(args)
        return {}

    result = simulate_round(
        proposals, incumbent_id="H_t", incumbent_score=0.50,
        incumbent_cost=100, s_star=0.50, delta=0.01,
        study_task_ids=StudyTaskSet(("s1",), trusted_runtime=True), trials=1,
        critic=lambda proposal: {"verdict": "reject", "reasons": ["leak"]},
        evaluate_many=evaluate_many,
    )
    assert eval_calls == []
    assert result.selection.winner_id is None
    assert result.selection.incumbent_retained is True
    assert result.next_parent == "H_t"
    assert all(not d.admissible for d in result.selection.decisions)


def test_numeric_domain_and_identity_validation_rejects_nonfinite_or_invalid_inputs():
    with pytest.raises(ValueError, match="finite"):
        Candidate("bad", score=float("inf"), cost=1)
    with pytest.raises(ValueError, match="non-negative"):
        Candidate("bad", score=0.5, cost=-1)
    with pytest.raises(ValueError, match="unique"):
        select_candidates([Candidate("A", score=.6, cost=1),
                           Candidate("A", score=.7, cost=1)],
                          incumbent_id="H0", incumbent_score=.5,
                          incumbent_cost=1, s_star=.5, delta=.1)
    with pytest.raises(ValueError, match="collides"):
        select_candidates([Candidate("H0", score=.6, cost=1)],
                          incumbent_id="H0", incumbent_score=.5,
                          incumbent_cost=1, s_star=.5, delta=.1)


def test_context_and_study_task_boundaries_are_typed():
    with pytest.raises(TypeError):
        role_prompt("analyst", "SECRET_EXAM score=1")
    with pytest.raises(ValueError, match="trusted"):
        TrainContext("evolve report")
    with pytest.raises(ValueError, match="trusted"):
        StudyTaskSet(("train",))
    with pytest.raises(ValueError, match="evolve/train"):
        TrainContext("SECRET_EXAM score=1", source="heldout", trusted_runtime=True)
    with pytest.raises(TypeError):
        simulate_round([], incumbent_id="H0", incumbent_score=.5,
                       incumbent_cost=1, s_star=.5, delta=.1,
                       study_task_ids=["train"], trials=1,
                       critic=lambda _: {"verdict": "accept"},
                       evaluate_many=lambda *_: {})


def test_missing_or_unattested_measurement_cannot_be_promoted():
    result = simulate_round(
        [Proposal("A")], incumbent_id="H0", incumbent_score=.5,
        incumbent_cost=100, s_star=.5, delta=.01,
        study_task_ids=StudyTaskSet(("s1",), trusted_runtime=True), trials=2,
        critic=lambda _: {"verdict": "accept"},
        evaluate_many=lambda *_: {"A": {"score": .99, "cost": 1,
                                          "measured": False}},
    )
    assert result.candidates[0].evaluated is False
    assert result.selection.parent_id == "H0"
    assert result.selection.winner_id is None


def test_evaluation_identity_presence_and_gate_status_are_attested():
    common = dict(
        incumbent_id="H0", incumbent_score=.5, incumbent_cost=100,
        s_star=.5, delta=.01, study_task_ids=StudyTaskSet(("s1",), trusted_runtime=True), trials=1,
        critic=lambda _: {"verdict": "accept"},
    )
    with pytest.raises(ValueError, match="candidate_id mismatch"):
        simulate_round([Proposal("A")], evaluate_many=lambda *_: {
            "A": {"candidate_id": "B", "score": .99, "cost": 1}}, **common)
    with pytest.raises(ValueError, match="unknown candidate IDs"):
        simulate_round([Proposal("A")], evaluate_many=lambda *_: {
            "A": {"score": .99, "cost": 1}, "B": {"score": .99, "cost": 1}},
                       **common)
    missing = simulate_round([Proposal("A")], evaluate_many=lambda *_: {}, **common)
    assert missing.candidates[0].gate_failure == "evaluation_missing"
    gated = simulate_round([Proposal("A")], evaluate_many=lambda *_: {
        "A": {"score": .99, "cost": 1, "gate_failure": "smoke_fail"}}, **common)
    assert gated.candidates[0].gate_failure == "smoke_fail"
    assert gated.next_parent == "H0"
