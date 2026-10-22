from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harnessgrad.budget_dispatch import (
    BudgetDispatcher,
    BudgetExceeded,
    BudgetViolation,
    LedgerCorrupt,
    LedgerTampered,
    ProviderExecutionUnknown,
    StaleRequest,
)

PROFILE = {
    "profile_version": 1,
    "limits": {
        "wall_seconds": 30,
        "max_requests": 20,
        "max_calls": 20,
        "max_retries": 4,
        "max_prompt_tokens": 12,
        "max_output_tokens": 12,
        "global_total_tokens": 16,
        "max_context": 12,
    },
    "roles": {"worker": {"max_total_tokens": 8}},
}


def test_reserves_before_factory_and_forces_bounded_request(tmp_path: Path):
    dispatcher = BudgetDispatcher(PROFILE, tmp_path / "ledger.json")
    seen = []

    def factory(request):
        seen.append(request)
        return lambda payload, max_tokens, timeout: {
            "payload": payload,
            "max_tokens": max_tokens,
            "usage": {"prompt_tokens": request.prompt_tokens, "completion_tokens": 2},
        }

    result = dispatcher.dispatch(factory, {"offline": True}, "worker", 3, 4)
    assert result["max_tokens"] == 4
    assert seen[0].max_tokens == 4
    assert dispatcher.snapshot()["spent"]["total_tokens"] == 5


def test_openai_sdk_usage_object_is_reconciled(tmp_path: Path):
    dispatcher = BudgetDispatcher(PROFILE, tmp_path / "ledger.json")

    class Usage:
        prompt_tokens = 2
        completion_tokens = 1
        total_tokens = 3

    class Response:
        usage = Usage()

        def __init__(self):
            self.choices = []

    result = dispatcher.dispatch_with_result(lambda: lambda payload, **kwargs: Response(), {}, "worker", 2, 2)
    assert result.usage == {"prompt_tokens": 2, "output_tokens": 1, "total_tokens": 3}
    assert dispatcher.snapshot()["spent"]["total_tokens"] == 3


def test_unknown_usage_blocks_following_token_calls_and_no_connection_release(tmp_path: Path):
    dispatcher = BudgetDispatcher(PROFILE, tmp_path / "ledger.json")
    with pytest.raises(ProviderExecutionUnknown):
        dispatcher.dispatch(lambda: (_ for _ in ()).throw(ConnectionError("offline")),
                            {}, "worker", 1, 2)
    with pytest.raises(BudgetExceeded, match="unknown_usage"):
        dispatcher.dispatch(lambda: lambda payload, **kwargs: {}, {}, "worker", 1, 1)
    request_id = dispatcher.last_request_id
    dispatcher.reconcile(request_id, None, provider_did_not_execute=True)
    dispatcher.dispatch(lambda: lambda payload, **kwargs: {
        "usage": {"prompt_tokens": 1, "completion_tokens": 0}}, {}, "worker", 1, 0)


def test_usage_larger_than_reservation_records_violation(tmp_path: Path):
    dispatcher = BudgetDispatcher(PROFILE, tmp_path / "ledger.json")
    result = dispatcher.dispatch(lambda: lambda payload, **kwargs: {}, {}, "worker", 1, 1)
    del result
    with pytest.raises(BudgetViolation):
        dispatcher.reconcile(dispatcher.last_request_id, {
            "prompt_tokens": 2, "completion_tokens": 1})
    state = dispatcher.snapshot()
    assert state["violations"] and state["unknown_requests"]


def test_profile_and_ledger_integrity_survive_restart_and_reject_tampering(tmp_path: Path):
    path = tmp_path / "ledger.json"
    first = BudgetDispatcher(PROFILE, path, run_id="r1")
    first.dispatch(lambda: lambda payload, **kwargs: {
        "usage": {"prompt_tokens": 1, "completion_tokens": 0}}, {}, "worker", 1, 0)
    assert BudgetDispatcher(PROFILE, path, run_id="r1").snapshot()["spent"]["prompt_tokens"] == 1
    with pytest.raises(LedgerTampered):
        BudgetDispatcher({**PROFILE, "limits": {**PROFILE["limits"], "max_calls": 19}}, path, run_id="r1")
    raw = json.loads(path.read_text())
    raw["counts"]["calls"] = 0
    path.write_text(json.dumps(raw))
    with pytest.raises(LedgerTampered):
        BudgetDispatcher(PROFILE, path, run_id="r1")


def test_corrupt_and_stale_ids_reject(tmp_path: Path):
    path = tmp_path / "ledger.json"
    dispatcher = BudgetDispatcher(PROFILE, path)
    with pytest.raises(StaleRequest):
        dispatcher.reconcile("req-missing", None)
    path.write_text("{")
    with pytest.raises(LedgerCorrupt):
        dispatcher.snapshot()
    with pytest.raises(LedgerCorrupt):
        dispatcher.reconcile("req-missing", None)


def _reserve_once(path: str, output: list):
    try:
        d = BudgetDispatcher({"limits": {"global_total_tokens": 4}}, path)
        d.dispatch(lambda: (_ for _ in ()).throw(ConnectionError("offline")), {}, "worker", 2, 2)
    except (BudgetExceeded, ProviderExecutionUnknown) as exc:
        output.append(type(exc).__name__)


def test_process_lock_prevents_concurrent_overspend(tmp_path: Path):
    manager = multiprocessing.Manager()
    output = manager.list()
    path = str(tmp_path / "ledger.json")
    jobs = [multiprocessing.Process(target=_reserve_once, args=(path, output)) for _ in range(2)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(10)
    assert sorted(output) == ["BudgetExceeded", "ProviderExecutionUnknown"]
