"""Pure trusted-controller transport for public stepwise observations.

The session stays with the controller; only JSON text reaches ``deliver``. The
caller supplies a reviewed public field allowlist and separate delivery/receive
functions. In production those functions must communicate with an isolated actor
process, enforce I/O deadlines and persist controller-owned receipts. In-process
test callbacks establish ordering, not process isolation or measured accuracy.

This module has no dataset imports, loaders, scoring, model calls or I/O.
``unix_stepwise_relay`` supplies bounded live I/O for opt-in runner tasks; ordinary
QA tasks still collect answer.txt after exit. Transport receipts never authorize
production execution.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy
import json
from typing import Protocol


class PublicStepwiseSession(Protocol):
    """Controller-owned session; private state is never an actor argument."""

    def public_task(self) -> dict: ...

    def submit(self, answer: dict) -> dict: ...


class StepwiseTransportError(RuntimeError):
    """Incomplete exchange with the controller's partial, unmeasured receipt."""

    def __init__(self, code: str, receipt: dict):
        super().__init__(code)
        self.receipt = deepcopy(receipt)
        self.receipt.update(status="incomplete", reason=code)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate answer field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON value")


def transport_stepwise(
    session: PublicStepwiseSession,
    deliver: Callable[[str], None],
    receive: Callable[[], str],
    *,
    contract: str,
    public_fields: Iterable[str],
    max_submissions: int,
    max_message_bytes: int = 4096,
    awaiting_status: str = "awaiting_move",
    terminal_statuses: Iterable[str] = ("solved", "failed"),
) -> dict:
    """Deliver each public board/status/verdict before receiving another answer.

    ``deliver`` must return only after sending the observation; terminal
    observations are delivered without requesting another answer. Only bounded,
    flat JSON objects with exactly ``public_fields`` may leave the controller.
    Fields and status names are caller-owned configuration, never actor input.
    Answers are JSON objects; their task-specific validation belongs to ``submit``.

    Callback, session, protocol and submission-limit failures raise an error with
    a partial receipt, never a score or an ordinary failed-task verdict. Message
    and submission limits bound retained records; blocking I/O needs an external
    deadline. Receipts describe exchanges and do not attest actor isolation.
    """
    fields = frozenset(public_fields)
    terminals = frozenset(terminal_statuses)
    if (not isinstance(contract, str) or not contract
            or not {"contract", "status"} <= fields
            or any(type(field) is not str or not field for field in fields)
            or type(max_submissions) is not int or max_submissions <= 0
            or type(max_message_bytes) is not int or max_message_bytes <= 0
            or type(awaiting_status) is not str or not awaiting_status
            or not terminals or awaiting_status in terminals
            or any(type(status) is not str or not status for status in terminals)):
        raise ValueError("invalid public stepwise transport configuration")
    receipt = {
        "schema": "harnessgrad.public-stepwise-transport.v1",
        "contract": contract,
        "status": "incomplete",
        "measured": False,
        "production_authorized": False,
        "submissions": 0,
        "limits": {"max_submissions": max_submissions,
                   "max_message_bytes": max_message_bytes},
        "events": [],
    }

    def fail(code: str) -> None:
        raise StepwiseTransportError(code, receipt)

    def bounded_text(value: object) -> bool:
        try:
            return type(value) is str and len(value.encode("utf-8")) <= max_message_bytes
        except UnicodeError:
            return False

    try:
        observation = session.public_task()
    except Exception:
        fail("session_observation_failed")

    while True:
        # Reject unreviewed fields, rather than serializing private session state.
        if (type(observation) is not dict or set(observation) != fields
                or any(type(value) not in (str, int, float, bool, type(None))
                       for value in observation.values())):
            fail("invalid_public_observation")
        if observation["contract"] != contract:
            fail("observation_contract_mismatch")
        status = observation["status"]
        if type(status) is not str or status not in terminals | {awaiting_status}:
            fail("invalid_observation_status")
        try:
            wire = json.dumps(observation, sort_keys=True, separators=(",", ":"),
                              allow_nan=False, ensure_ascii=True)
        except (ValueError, TypeError, OverflowError):
            fail("invalid_public_observation")
        if not bounded_text(wire):
            fail("observation_message_limit")
        try:
            deliver(wire)
        except Exception:
            fail("observation_delivery_failed")
        receipt["events"].append({"kind": "observation", "message": wire})
        if status in terminals:
            receipt["status"] = "terminal"
            return receipt
        if receipt["submissions"] >= max_submissions:
            fail("submission_limit")
        try:
            answer_wire = receive()
        except Exception:
            fail("answer_receive_failed")
        if not bounded_text(answer_wire):
            fail("invalid_or_oversized_answer_message")
        receipt["events"].append({"kind": "submission", "message": answer_wire})
        try:
            answer = json.loads(answer_wire, object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
        except (ValueError, TypeError, RecursionError):
            fail("invalid_answer_json")
        if type(answer) is not dict:
            fail("answer_object_required")
        receipt["submissions"] += 1
        try:
            observation = session.submit(answer)
        except Exception:
            fail("session_submission_failed")
