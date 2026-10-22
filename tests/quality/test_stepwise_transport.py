"""Fabricated observations only; no CSV, model, H0, network or process execution."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys

import chess
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import data.datasets.chessbench_puzzles as dataset
from eval.service.stepwise_transport import StepwiseTransportError, transport_stepwise


CHESS_FIELDS = frozenset({"contract", "fen", "solver_turn", "status", "verdict"})


@pytest.fixture(autouse=True)
def forbid_execution_and_payload_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("fabricated transport test attempted execution or dataset loading")

    for name in ("_read_rows", "_rows", "_selected_records", "load", "load_selected"):
        monkeypatch.setattr(dataset, name, forbidden)
    monkeypatch.setattr(dataset._SPEC, "_rows", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.suffix.lower() == ".csv":
            forbidden()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)


@pytest.fixture
def fabricated_gold():
    return {"fen": chess.STARTING_FEN,
            "moves": ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
            "puzzle_id": "fabricated-transport-only"}


def chess_transport(session, deliver, receive, **kwargs):
    return transport_stepwise(session, deliver, receive,
                              contract=dataset.STEPWISE_CONTRACT,
                              public_fields=CHESS_FIELDS,
                              max_submissions=kwargs.pop("max_submissions", 2), **kwargs)


def assert_unmeasured(receipt):
    assert receipt["measured"] is False
    assert receipt["production_authorized"] is False
    assert not {"score", "reward", "correct", "metric", "gold"} & set(receipt)


def test_chess_successive_boards_precede_decisions_and_terminal_is_delivered(fabricated_gold):
    session = dataset.StepwisePuzzle(fabricated_gold)
    board = chess.Board(chess.STARTING_FEN)
    board.push_uci("e2e4")
    first_fen = board.fen()
    board.push_uci("e7e5")
    board.push_uci("g1f3")
    second_fen = board.fen()
    board.push_uci("b8c6")
    board.push_uci("f1b5")
    final_fen = board.fen()
    # The fabricated actor picks from the most recently delivered board, rather
    # than precomputing a sequence or authoring the controller's transcript.
    choices = {first_fen: "e7e5", second_fen: "b8c6"}
    delivered, order = [], []

    def deliver(wire):
        assert type(wire) is str
        public = json.loads(wire)
        assert set(public) == CHESS_FIELDS
        delivered.append(public)
        order.append(("observation", public["solver_turn"]))

    def receive():
        public = delivered[-1]
        assert public["status"] == "awaiting_move"
        assert len(delivered) == len([item for item in order if item[0] == "decision"]) + 1
        order.append(("decision", public["solver_turn"]))
        return json.dumps({"uci": choices[public["fen"]]})

    receipt = chess_transport(session, deliver, receive)
    assert order == [("observation", 0), ("decision", 0),
                     ("observation", 1), ("decision", 1), ("observation", 2)]
    assert [public["fen"] for public in delivered] == [first_fen, second_fen, final_fen]
    assert [public["status"] for public in delivered] == ["awaiting_move", "awaiting_move", "solved"]
    assert [public["verdict"] for public in delivered] == ["awaiting_move", "awaiting_move", "reference_complete"]
    assert receipt["status"] == "terminal" and receipt["submissions"] == 2
    assert [event["kind"] for event in receipt["events"]] == [
        "observation", "submission", "observation", "submission", "observation"]
    assert [json.loads(event["message"]) for event in receipt["events"]
            if event["kind"] == "observation"] == delivered
    for public in delivered:
        wire = json.dumps(public)
        assert fabricated_gold["puzzle_id"] not in wire
        assert fabricated_gold["fen"] != public["fen"]
        assert all(move not in wire for move in fabricated_gold["moves"])
    assert_unmeasured(receipt)


@pytest.mark.parametrize("answer, verdict", [
    ({"uci": "e7e4"}, "illegal_move"),
    ({"uci": "b8c6"}, "wrong_move"),
    ({"uci": "e7e5 b8c6"}, "invalid_move"),
    ({"uci": "e7e5", "extra": True}, "invalid_answer"),
])
def test_task_answer_validation_delivers_failed_terminal(fabricated_gold, answer, verdict):
    delivered = []
    receipt = chess_transport(dataset.StepwisePuzzle(fabricated_gold), delivered.append,
                              lambda: json.dumps(answer))
    terminal = json.loads(delivered[-1])
    assert terminal["status"] == "failed" and terminal["verdict"] == verdict
    assert receipt["status"] == "terminal" and receipt["submissions"] == 1
    assert len(receipt["events"]) == 3
    assert_unmeasured(receipt)


class FabricatedSession:
    """No gold or chess code: prove the adapter works with another public contract."""

    def __init__(self, observation=None):
        self.observation = observation if observation is not None else {
            "contract": "fabricated.counter.v1", "status": "waiting", "count": 0}
        self.answers = []

    def public_task(self):
        return self.observation

    def submit(self, answer):
        self.answers.append(deepcopy(answer))
        self.observation.update(count=self.observation["count"] + answer["increment"],
                                status="done")
        return self.observation


def generic_transport(session, deliver, receive, **kwargs):
    return transport_stepwise(session, deliver, receive, contract="fabricated.counter.v1",
                              public_fields={"contract", "status", "count"},
                              awaiting_status="waiting", terminal_statuses={"done"},
                              max_submissions=kwargs.pop("max_submissions", 1), **kwargs)


def test_generic_transport_serializes_without_sharing_session_objects():
    session = FabricatedSession()
    delivered = []

    def deliver(wire):
        decoded = json.loads(wire)
        delivered.append(decoded)
        decoded["count"] = -100  # Changing the actor's copy cannot change the session.

    receipt = generic_transport(session, deliver, lambda: '{"increment":1}')
    public = [json.loads(event["message"]) for event in receipt["events"]
              if event["kind"] == "observation"]
    assert [item["count"] for item in public] == [0, 1]
    assert session.observation["count"] == 1 and session.answers == [{"increment": 1}]
    assert receipt["status"] == "terminal"
    assert_unmeasured(receipt)


@pytest.mark.parametrize("mutation, code", [
    ({"gold": "private-fabricated-value"}, "invalid_public_observation"),
    ({"count": ["private-fabricated-line"]}, "invalid_public_observation"),
    ({"contract": "other.contract.v1"}, "observation_contract_mismatch"),
    ({"status": "unknown"}, "invalid_observation_status"),
    ({"count": float("nan")}, "invalid_public_observation"),
])
def test_invalid_observation_never_reaches_actor(mutation, code):
    session = FabricatedSession()
    session.observation.update(mutation)
    delivered = []
    with pytest.raises(StepwiseTransportError, match=code) as error:
        generic_transport(session, delivered.append, lambda: pytest.fail("unexpected receive"))
    assert delivered == [] and session.answers == []
    assert error.value.receipt["events"] == []
    assert_unmeasured(error.value.receipt)


@pytest.mark.parametrize("wire, code", [
    ('{"increment":1,"increment":2}', "invalid_answer_json"),
    ('{"increment":NaN}', "invalid_answer_json"),
    ('{"increment":1} trailing', "invalid_answer_json"),
    ("not-json", "invalid_answer_json"),
    ("[]", "answer_object_required"),
    (None, "invalid_or_oversized_answer_message"),
    (b'{"increment":1}', "invalid_or_oversized_answer_message"),
    ("x" * 4097, "invalid_or_oversized_answer_message"),
    ('"\ud800"', "invalid_or_oversized_answer_message"),
])
def test_invalid_wire_is_incomplete_transport_never_a_task_zero(wire, code):
    session = FabricatedSession()
    with pytest.raises(StepwiseTransportError, match=code) as error:
        generic_transport(session, lambda message: None, lambda: wire)
    receipt = error.value.receipt
    assert receipt["status"] == "incomplete" and receipt["reason"] == code
    assert session.answers == [] and receipt["submissions"] == 0
    assert_unmeasured(receipt)


def test_submission_bound_preserves_last_delivered_board(fabricated_gold):
    delivered = []
    with pytest.raises(StepwiseTransportError, match="submission_limit") as error:
        chess_transport(dataset.StepwisePuzzle(fabricated_gold), delivered.append,
                        lambda: '{"uci":"e7e5"}', max_submissions=1)
    receipt = error.value.receipt
    assert receipt["status"] == "incomplete" and receipt["submissions"] == 1
    assert [event["kind"] for event in receipt["events"]] == [
        "observation", "submission", "observation"]
    assert json.loads(delivered[-1])["solver_turn"] == 1
    assert json.loads(delivered[-1])["status"] == "awaiting_move"
    assert_unmeasured(receipt)


def test_initial_terminal_is_delivered_without_receiving():
    session = FabricatedSession()
    session.observation["status"] = "done"
    delivered = []
    receipt = generic_transport(session, delivered.append, lambda: pytest.fail("terminal receive"))
    assert len(delivered) == 1 and receipt["submissions"] == 0
    assert receipt["status"] == "terminal"


@pytest.mark.parametrize("stage, code", [
    ("deliver", "observation_delivery_failed"),
    ("receive", "answer_receive_failed"),
    ("public_task", "session_observation_failed"),
    ("submit", "session_submission_failed"),
])
def test_callback_failures_preserve_partial_receipt_without_exception_text(stage, code, monkeypatch):
    session = FabricatedSession()

    def broken(*args):
        raise RuntimeError("private-fabricated-exception-detail")

    deliver = broken if stage == "deliver" else lambda wire: None
    receive = broken if stage == "receive" else lambda: '{"increment":1}'
    if stage in {"public_task", "submit"}:
        monkeypatch.setattr(session, stage, broken)
    with pytest.raises(StepwiseTransportError, match=code) as error:
        generic_transport(session, deliver, receive)
    receipt = error.value.receipt
    assert receipt["status"] == "incomplete" and receipt["reason"] == code
    assert "private-fabricated-exception-detail" not in json.dumps(receipt)
    expected_events = {"deliver": 0, "receive": 1, "public_task": 0, "submit": 2}
    assert len(receipt["events"]) == expected_events[stage]
    assert_unmeasured(receipt)


def test_changed_contract_after_submission_is_not_delivered(monkeypatch):
    session = FabricatedSession()
    monkeypatch.setattr(session, "submit", lambda answer: {
        "contract": "changed", "status": "done", "count": 1})
    delivered = []
    with pytest.raises(StepwiseTransportError, match="observation_contract_mismatch") as error:
        generic_transport(session, delivered.append, lambda: '{"increment":1}')
    assert len(delivered) == 1 and len(error.value.receipt["events"]) == 2


def test_observation_size_limit_applies_before_delivery():
    session = FabricatedSession()
    session.observation["count"] = "x" * 4096
    with pytest.raises(StepwiseTransportError, match="observation_message_limit") as error:
        generic_transport(session, lambda wire: pytest.fail("oversized delivery"),
                          lambda: pytest.fail("unexpected receive"))
    assert error.value.receipt["events"] == []
