"""The τ² control's three tasks are chosen for properties, and the properties are checked.

`data/datasets/tau2_bench/control.py` runs one released task per domain. The ids are not arbitrary:
each task must have assistant-only gold actions (no `requestor: "user"` step only the simulator could
perform), a gold action set that changes state (or a do-nothing episode would match it), no
`communicate_info` (or the arm would have to be scripted to say a phrase the scorer looks for), and
no `NL_ASSERTION` in its reward basis (or the fixture's non-judgement would carry part of the score).

This test reads the prepared payload, so it skips where the data is not provisioned. It does not run
the control -- `tests/quality/test_dataset_controls.py` runs both arms.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.datasets import tau2_bench as dataset  # noqa: E402


def _control():
    """Load the script by path: `data.datasets.tau2_bench` is the adapter module, and the
    directory of the same name is assets, not a package."""
    spec = importlib.util.spec_from_file_location('tau2_bench_control', dataset.ASSETS / 'control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _payload_present() -> bool:
    """Storage may be unconfigured entirely, in which case resolving the root raises."""
    try:
        return (dataset.DATASET_ROOT / "manifest.json").is_file()
    except Exception:  # noqa: BLE001 - an unconfigured root is simply "no payload here"
        return False


pytestmark = pytest.mark.skipif(not _payload_present(),
                                reason="no prepared τ² payload on this machine")

TASKS = _control().TASKS


def _criteria(tid: str) -> dict:
    row = dataset.roster()[tid]
    private = json.loads((dataset.DATASET_ROOT / row["path"] / "private.json").read_text())
    return private["task"]["evaluation_criteria"]


def test_the_control_covers_one_released_task_per_domain():
    domains = [dataset.roster()[tid]["domain"] for tid in TASKS]
    assert domains == list(dataset.DOMAINS)
    assert len(set(TASKS)) == 3


@pytest.mark.parametrize("tid", TASKS)
def test_each_control_task_is_one_only_the_agent_can_complete(tid):
    criteria = _criteria(tid)
    actions = criteria.get("actions") or []
    assert actions, tid
    assert [a for a in actions if a.get("requestor", "assistant") != "assistant"] == [], tid
    assert dataset.roster()[tid]["user_actions"] is False


@pytest.mark.parametrize("tid", TASKS)
def test_each_control_task_changes_state_or_assigns_the_action(tid):
    """A read-only gold action set would match an untouched database, so the noop arm would score
    1 -- a control that cannot fail is worse than no control."""
    criteria = _criteria(tid)
    names = {a["name"] for a in criteria["actions"]}
    mutating = {"book_reservation", "update_reservation_flights", "update_reservation_baggages",
                "update_reservation_passengers", "cancel_reservation", "send_certificate",
                "cancel_pending_order", "modify_pending_order_address",
                "modify_pending_order_items", "modify_pending_order_payment",
                "modify_user_address", "exchange_delivered_order_items",
                "return_delivered_order_items", "transfer_to_human_agents"}
    assert names & mutating, (tid, sorted(names))


@pytest.mark.parametrize("tid", TASKS)
def test_each_control_task_keeps_the_phrase_and_judge_halves_out(tid):
    criteria = _criteria(tid)
    assert not criteria.get("communicate_info"), tid
    basis = criteria.get("reward_basis") or ["DB", "COMMUNICATE"]
    assert "NL_ASSERTION" not in basis, (tid, basis)


def test_the_airline_arm_is_the_one_that_exercises_the_judge_declaration():
    """Airline has `nl_assertions`, so `EvaluationType.ALL` calls a judge whose verdict the basis
    then multiplies out. Keeping one such task in the control means the declared judge channel is
    exercised rather than merely declared."""
    assert dataset.roster()["tau2_bench/airline/8"]["needs_judge"] is True
    assert dataset.roster()["tau2_bench/retail/0"]["needs_judge"] is False


def test_the_control_refuses_a_task_it_cannot_judge():
    """The refusal is by property, not by id: a task whose *reward* needs a judge is rejected."""
    control = _control()
    for tid in TASKS:
        control._gold_actions(tid)  # no raise
    source = (dataset.ASSETS / "control.py").read_text(encoding="utf-8")
    assert "control requires a task whose reward does not depend on a judge" in source
