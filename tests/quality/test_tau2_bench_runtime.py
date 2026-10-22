"""The τ² runtime pieces, pinned at the source level.

These run on the platform's Python 3.12 and never import `tau2` (that needs 3.13 and the isolated
runtime), so they can sit in the normal gate. What they pin is the part a later edit could quietly
break: the operation set the actor can reach, the two guards the scorer carries, and the fact that
the judge is bound by patching a module rather than by passing a parameter.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "data" / "datasets" / "tau2_bench"


def source(relative: str) -> str:
    return (ASSETS / relative).read_text(encoding="utf-8")


def test_the_relay_exposes_exactly_the_five_half_duplex_operations():
    """`show` is a file read in the CLI; the socket carries the conversation only."""
    text = source("relay.py")
    for operation in ("'start'", "'say'", "'call'", "'calls'", "'status'"):
        assert operation in text, operation
    assert "unsupported_operation_or_fields" in text


def test_the_relay_checks_both_task_bindings():
    """The platform id the caller asked for *and* the upstream id inside the private row."""
    text = source("relay.py")
    assert "context['task_id']" in text and "private.get('id')" in text
    assert "relay task binding mismatch" in text


def test_the_scorer_refuses_a_basis_with_nothing_in_it():
    """v0.1.0 returns 1.0 for `all([])`; the guard is the point of the module."""
    text = source("lib/scoring.py")
    assert "no_actionable_reward_basis" in text
    assert "actionable_basis" in text
    # The enum trap: `str(RewardType.DB)` is "RewardType.DB", so the value must be taken explicitly.
    assert "getattr(entry, 'value', entry)" in text


def test_an_unfinished_episode_is_scored_through_the_early_termination_rule():
    """v0.1.0 has no AGENT_ERROR, so the substitution is recorded rather than silent."""
    text = source("lib/scoring.py")
    assert "incomplete_conversation_without_stop_signal" in text
    assert "TerminationReason.MAX_STEPS.value" in text
    assert "termination_substitution" in text


def test_the_judge_is_bound_by_patching_a_module_not_by_a_parameter():
    text = source("lib/model.py")
    assert "malformed_nl_judge_result" in text
    assert "module.generate = generate" in text
    assert "HG_ENV_MODEL_SOCKET" in text and "HG_JUDGE_SOCKET" in text


def test_the_runtime_mirrors_the_official_loop_order():
    """assistant -> user, assistant -> environment, and the official budgets."""
    text = source("lib/runtime.py")
    assert "MAX_STEPS = 100" in text and "MAX_ERRORS = 10" in text
    for method in ("start_conversation", "submit_assistant_message", "call_assistant_tool",
                   "submit_assistant_tool_calls", "get_runtime_status", "episode"):
        assert f"def {method}" in text, method
    assert "MultiToolMessage" in text          # several business calls in one turn
    assert "sync_tools" in text                # tau2 resyncs after every environment call


def test_every_new_module_parses():
    for relative in ("relay.py", "cli/tau2.py", "lib/bootstrap.py", "lib/model.py",
                     "lib/runtime.py", "lib/scoring.py", "verify/check.py", "prepare.py"):
        ast.parse(source(relative), filename=relative)
    assert not (ASSETS / "REFUSED").exists()
