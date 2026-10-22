"""Offline contracts for the driver-owned trusted dispatcher binding."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harnessgrad.budget_dispatch import BudgetExceeded, ProviderExecutionUnknown
from harnessgrad.budget_integration import (
    BudgetConfigurationError,
    ProviderBoundary,
    bind_budget,
)

PROFILE = {
    "profile_version": 1,
    "limits": {
        "wall_seconds": 30,
        "max_requests": 4,
        "max_calls": 3,
        "max_retries": 1,
        "max_prompt_tokens": 20,
        "max_output_tokens": 10,
        "global_total_tokens": 24,
        "max_context": 12,
        "candidates": 2,
        "rounds": 2,
        "trials": 2,
        "jobs": 1,
    },
    "roles": {"agent": {"max_total_tokens": 20}},
}


def test_binding_reserves_before_factory_and_records_separate_units(tmp_path: Path):
    binding = bind_budget(PROFILE, tmp_path / "ledger.json", run_id="r1", arm_id="agent")
    assert binding is not None
    binding.record("candidates", 1)
    seen = []

    def factory(request):
        assert json.loads((tmp_path / "ledger.json").read_text())["counts"]["calls"] == 1
        seen.append(request)
        return lambda payload, max_tokens, timeout: {
            "value": payload, "max_tokens": max_tokens,
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    assert binding.dispatch(factory, {"offline": True}, "agent", 3, 4)["max_tokens"] == 4
    assert seen[0].timeout is not None
    counters = binding.counters()
    assert counters["calls"] == 1 and counters["attempts"] == 1
    assert counters["prompt_tokens"] == 3 and counters["output_tokens"] == 2
    assert counters["candidates"] == 1 and counters["rounds"] == 0


def test_binding_identity_and_ledger_are_immutable(tmp_path: Path):
    path = tmp_path / "ledger.json"
    first = bind_budget(PROFILE, path, run_id="r1", arm_id="agent")
    assert first is not None
    assert first.metadata()["profile_hash"] == first.profile_hash
    with pytest.raises(BudgetConfigurationError):
        bind_budget({**PROFILE, "limits": {**PROFILE["limits"], "max_calls": 2}},
                    path, run_id="r1", arm_id="agent")
    with pytest.raises(BudgetConfigurationError):
        bind_budget(PROFILE, path, run_id="r1", arm_id="critic")


def test_unknown_usage_and_provider_error_remain_unmeasured(tmp_path: Path):
    binding = bind_budget(PROFILE, tmp_path / "ledger.json", run_id="r1")
    assert binding is not None
    with pytest.raises(ProviderExecutionUnknown):
        binding.dispatch(lambda: (_ for _ in ()).throw(ConnectionError("offline")),
                         {}, "agent", 1, 1)
    assert binding.counters()["prompt_tokens"] is None
    with pytest.raises(BudgetExceeded, match="unknown_usage"):
        binding.dispatch(lambda: lambda payload, **kwargs: {}, {}, "agent", 1, 1)


def test_retry_is_linked_and_no_execution_proof_releases_reservation(tmp_path: Path):
    binding = bind_budget(PROFILE, tmp_path / "ledger.json", run_id="r1")
    assert binding is not None
    result = binding.dispatch(lambda: lambda payload, **kwargs: {
        "usage": {"prompt_tokens": 1, "completion_tokens": 0}}, {}, "agent", 1, 1)
    assert result["usage"] == {"prompt_tokens": 1, "completion_tokens": 0}
    settled_id = binding.dispatcher.last_request_id
    assert settled_id is not None
    retry = binding.dispatcher.dispatch(lambda: lambda payload, **kwargs: {
        "usage": {"prompt_tokens": 1, "completion_tokens": 0}}, {}, "agent", 1, 1,
        retry_of=settled_id)
    assert retry["usage"]["prompt_tokens"] == 1
    binding.dispatcher.dispatch(lambda: lambda payload, **kwargs: {}, {}, "agent", 1, 1)
    unknown_id = binding.dispatcher.last_request_id
    assert unknown_id is not None
    binding.dispatcher.reconcile(unknown_id, None, provider_did_not_execute=True)
    state = binding.dispatcher.snapshot()
    assert state["requests"][unknown_id]["status"] == "abandoned"


def test_deadline_is_bounded_and_refusal_happens_before_factory(tmp_path: Path):
    profile = {**PROFILE, "limits": {**PROFILE["limits"], "wall_seconds": 0}}
    binding = bind_budget(profile, tmp_path / "ledger.json", run_id="r1")
    assert binding is not None
    called = []
    with pytest.raises(BudgetExceeded, match="wall"):
        binding.dispatch(lambda: called.append(True), {}, "agent", 1, 1)
    assert called == []


def test_context_and_unit_caps_refuse_before_factory(tmp_path: Path):
    binding = bind_budget(PROFILE, tmp_path / "ledger.json", run_id="r1")
    assert binding is not None
    called = []
    with pytest.raises(BudgetExceeded, match="max_context"):
        binding.dispatch(lambda: called.append(True), {}, "agent", 9, 4)
    assert not called
    binding.record("candidates", 2)
    with pytest.raises(BudgetExceeded, match="agent.candidates"):
        binding.record("candidates")


def test_legacy_boundary_has_no_profile_or_budget(tmp_path: Path):
    boundary = ProviderBoundary()
    calls = []

    def factory(payload):
        calls.append(payload)
        return lambda value: {"value": value}

    assert boundary.dispatch(factory, "legacy", "agent", 1, 1) == {"value": "legacy"}
    assert calls == ["legacy"]
    assert boundary.metadata() is None


def test_missing_profile_ledger_is_a_configuration_error(tmp_path: Path):
    with pytest.raises(BudgetConfigurationError):
        bind_budget(PROFILE, None, run_id="r1")
    with pytest.raises(BudgetConfigurationError):
        bind_budget("{bad json", tmp_path / "ledger.json", run_id="r1")
