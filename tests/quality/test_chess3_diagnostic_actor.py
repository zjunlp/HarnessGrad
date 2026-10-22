"""Run the unmodified diagnostic actor on fabricated boards, never released data."""
import json
from pathlib import Path
import shutil

import chess
import pytest

from data.datasets import chessbench_puzzles as dataset
from eval.run.runner import run_one


@pytest.mark.parametrize("moves, status, verdict, submissions", [
    (["e2e4", "a7a5", "a2a3", "a5a4"], "solved", "reference_complete", 2),
    (["e2e4", "e7e5"], "failed", "wrong_move", 1),
])
def test_control_sends_at_zero_retains_connection_and_reads_terminal(
        tmp_path, monkeypatch, moves, status, verdict, submissions):
    def forbidden(*args, **kwargs):
        pytest.fail("synthetic actor control attempted dataset loading")

    for name in ("_read_rows", "_rows", "_selected_records", "load", "load_selected"):
        monkeypatch.setattr(dataset, name, forbidden)
    monkeypatch.setattr(dataset._SPEC, "ensure", forbidden)
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    repo = tmp_path / "control"
    shutil.copytree(Path(__file__).resolve().parents[2] / "reports/w-chess3-control", repo)
    gold = {"fen": chess.STARTING_FEN, "moves": moves, "puzzle_id": "synthetic-only"}
    task = {"task_id": "chessbench_puzzles/test/0", "goal": "synthetic",
            "inputs": [], "interaction": dataset.STEPWISE_CONTRACT}
    result = run_one(repo, task, sandbox=True,
                     verifier=dataset.verify_for({"task_id": task["task_id"], "gold": gold}),
                     timeout_s=5)
    assert result["exit_code"] == 0, result["stderr"]
    receipt = result["verdict"]["evidence"]
    assert receipt["status"] == "complete" and receipt["measured"] is True
    assert receipt["connections"] == 1 and receipt["rejected_connections"] == 0
    assert receipt["transport"]["submissions"] == submissions
    assert all(receipt["cleanup"].values())
    events = [json.loads(line) for line in result["trace"].splitlines()]
    observations = [e["observation"] for e in events if e["event"] == "observation"]
    assert observations[0]["solver_turn"] == 0
    assert observations[-1]["status"] == status
    assert observations[-1]["verdict"] == verdict
    assert events[-1] == {"event": "completed", "terminal_received": True, "channel_closed": True}
    sent = [e for e in events if e["event"] == "submission"]
    assert len(sent) == submissions and sent[0]["solver_turn"] == 0
    for observation, event in zip(observations, sent):
        board = chess.Board(observation["fen"])
        assert chess.Move.from_uci(event["answer"]["uci"]) in board.legal_moves
    delivered = [json.loads(e["message"]) for e in receipt["transport"]["events"]
                 if e["kind"] == "observation"]
    assert observations == delivered
