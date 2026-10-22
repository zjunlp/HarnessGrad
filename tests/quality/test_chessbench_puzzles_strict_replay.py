"""Synthetic-only fidelity/privacy checks. Never load a released puzzle or run a model."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import chess
import pytest

import data.datasets.chessbench_puzzles as dataset


@pytest.fixture(autouse=True)
def forbid_dataset_reads(monkeypatch):
    def forbidden():
        pytest.fail("synthetic tests must not read the puzzle CSV")

    monkeypatch.setattr(dataset, "_read_rows", forbidden)


@pytest.fixture
def gold():
    return {"fen": chess.STARTING_FEN,
            "moves": ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
            "puzzle_id": "synthetic-opening"}


def test_legal_sequence(gold):
    result = dataset.score("e7e5 b8c6", gold)
    assert result["reward"] == 1.0 and result["correct"]
    assert result["verdict"] == "reference_complete"
    assert result["solver_moves_played"] == 2
    assert result["official"]["upstream"]["reward"] == 1.0


@pytest.mark.parametrize("answer,verdict", [
    ("e7e4", "illegal_move"),       # Impossible three-square pawn move.
    ("e2e4", "illegal_move"),       # Wrong side / empty source after setup.
    ("0000", "illegal_move"),       # python-chess can push a null move.
    ("not-uci", "invalid_move"),
    ("e7e5 not-uci", "invalid_move"),
    ("b8c6 e7e5", "wrong_move"),    # Legal, but wrong order and no checkmate.
    ("e7e5 a7a6", "wrong_move"),    # Wrong second solver move after reference reply.
    ("e7e5", "missing_move"),
    ("", "missing_move"),
])
def test_bad_sequence_has_explicit_verdict(gold, answer, verdict):
    result = dataset.score(answer, gold)
    assert result["reward"] == 0.0 and not result["correct"]
    assert result["verdict"] == verdict
    assert verdict in result["why"]


def test_illegal_move_does_not_change_board(gold):
    session = dataset.StepwisePuzzle(gold)
    before = session.public_task()["fen"]
    result = session.submit({"uci": "e7e4"})
    assert result["status"] == "failed" and result["verdict"] == "illegal_move"
    assert result["fen"] == before and result["solver_turn"] == 0


def test_self_check_is_illegal():
    gold = {"fen": "4r1k1/p7/8/8/8/8/4R3/4K3 b - - 0 1",
            "moves": ["a7a6", "e2e3"], "puzzle_id": "synthetic-pin"}
    result = dataset.score("e2f2", gold)
    assert result["verdict"] == "illegal_move" and result["reward"] == 0.0


@pytest.mark.parametrize("moves", [[], ["e2e4"], ["e2e5", "e7e5"],
                                    ["e2e4", "e7e5", "g1g5", "b8c6"]])
def test_invalid_setup_or_opponent_reference_is_explicit(moves):
    gold = {"fen": chess.STARTING_FEN, "moves": moves,
            "puzzle_id": "synthetic-invalid-reference"}
    result = dataset.score("e7e5 b8c6", gold)
    assert result["reward"] == 0.0 and result["verdict"] == "invalid_reference"


@pytest.mark.parametrize("fen", ["not-fen", "8/8/8/8/8/8/8/8 w - - 0 1"])
def test_invalid_reference_board_is_explicit(fen):
    gold = {"fen": fen, "moves": ["e2e4", "e7e5"], "puzzle_id": "synthetic"}
    result = dataset.score("e7e5", gold)
    assert result["verdict"] == "invalid_reference" and result["reward"] == 0.0


@pytest.mark.parametrize("answer,verdict", [("g6g7", "checkmate"),
                                          ("g6g8", "alternate_mate_in_one")])
def test_early_legal_mate_terminates_before_unused_reference_or_answers(answer, verdict):
    gold = {"fen": "7k/5K2/6Q1/8/p7/8/8/8 b - - 0 1",
            "moves": ["a4a3", "g6g7", "h8h7", "g7h7"],
            "puzzle_id": "synthetic-early-mate"}
    result = dataset.score(answer + " not-uci", gold)
    assert result["reward"] == 1.0 and result["verdict"] == verdict
    assert result["solver_moves_played"] == 1
    session = dataset.StepwisePuzzle(gold)
    assert session.submit({"uci": answer})["status"] == "solved"
    with pytest.raises(ValueError, match="already terminal"):
        session.submit({"uci": "a3a2"})


def test_unused_trailing_tokens_match_engine_call_semantics(gold):
    result = dataset.score("e7e5 b8c6 not-uci", gold)
    assert result["reward"] == result["official"]["upstream"]["reward"] == 1.0


def test_upstream_unchecked_push_acceptance_is_diagnostic_only():
    gold = {"fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e4"],
            "puzzle_id": "synthetic-illegal-reference"}
    result = dataset.score("e7e4", gold)
    metrics = result["official"]
    assert result["reward"] == metrics["strict"]["reward"] == 0.0
    assert metrics["strict"]["verdict"] == "illegal_move"
    assert metrics["upstream"]["reward"] == 1.0
    assert metrics["upstream"]["correct"]
    assert metrics["metric"] == dataset.STRICT_METRIC
    assert metrics["upstream"]["metric"] == dataset.UPSTREAM_METRIC
    assert "not interactive official accuracy" in metrics["protocol"]


def test_every_strict_push_is_legal(monkeypatch, gold):
    original = chess.Board.push
    applied = []

    def checked_push(board, move):
        assert move in board.legal_moves
        applied.append(move.uci())
        return original(board, move)

    monkeypatch.setattr(chess.Board, "push", checked_push)
    assert dataset.score_strict("e7e5 b8c6", gold)["reward"] == 1.0
    assert applied == gold["moves"]


def test_stepwise_public_contract_updates_after_each_reference_reply(gold):
    session = dataset.StepwisePuzzle(gold)
    expected = chess.Board(gold["fen"])
    expected.push_uci("e2e4")
    first = session.public_task()
    assert first == {"contract": dataset.STEPWISE_CONTRACT, "fen": expected.fen(),
                     "solver_turn": 0, "status": "awaiting_move", "verdict": "awaiting_move"}
    second = session.submit({"uci": "e7e5"})
    expected.push_uci("e7e5")
    expected.push_uci("g1f3")
    assert second["fen"] == expected.fen()
    assert second["solver_turn"] == 1 and second["status"] == "awaiting_move"
    # The next solver decision is made from the opponent-updated position.
    last = session.submit({"uci": "b8c6"})
    expected.push_uci("b8c6")
    expected.push_uci("f1b5")
    assert last["fen"] == expected.fen() and last["status"] == "solved"
    for public in (first, second, last):
        assert set(public) == {"contract", "fen", "solver_turn", "status", "verdict"}
        text = json.dumps(public)
        assert "synthetic-opening" not in text
        assert all(move not in text for move in gold["moves"])


@pytest.mark.parametrize("answer", ["e7e5", {}, {"uci": 1},
                                    {"uci": "e7e5", "moves": ["b8c6"]},
                                    {"uci": "e7e5 b8c6"}])
def test_stepwise_answer_is_exactly_one_uci_move(gold, answer):
    session = dataset.StepwisePuzzle(gold)
    public = session.submit(answer)
    assert public["status"] == "failed"
    assert public["verdict"] in {"invalid_answer", "invalid_move"}


def test_batch_goal_is_public_and_names_variant(gold):
    question = dataset._question({"presented_fen": dataset.StepwisePuzzle(gold).public_task()["fen"]})
    goal = dataset._goal(question, gold)
    assert "offline strict sequence variant" in goal
    assert "not interactive official puzzle accuracy" in goal
    assert "answer.txt" in goal and "qa submit" in goal
    assert gold["puzzle_id"] not in goal
    assert gold["fen"] not in goal
    assert " ".join(gold["moves"]) not in goal


def test_import_never_attempts_to_read_puzzle_csv():
    # A fresh interpreter also catches attempted reads swallowed by QASpec.ensure().
    script = """
from pathlib import Path
attempted = []
original = Path.open
def guarded(path, *args, **kwargs):
    if path.name == 'puzzles.csv':
        attempted.append(str(path))
        raise AssertionError('no puzzle payload reads')
    return original(path, *args, **kwargs)
Path.open = guarded
import data.datasets.chessbench_puzzles as dataset
assert attempted == [], attempted
assert dataset._SPEC._records == []
assert dataset._SPEC.load_error is None
assert dataset.DEFAULT_ENV == {'kind': 'files'}
assert dataset.OFFICIAL_EVAL is None
assert dataset.SPLIT_SOURCE == 'none'
assert dataset.SPLIT is None, 'one released puzzle slice: it declares no division'
"""
    subprocess.run([sys.executable, "-c", script], check=True,
                   cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)


def test_explicit_pool_loading_keeps_qa_metadata_and_tasks_compatible(monkeypatch, gold):
    # Exercise the deferred QA builder using only fabricated rows.
    row = {"fen": gold["fen"], "moves": gold["moves"], "rating": 0,
           "presented_fen": dataset.StepwisePuzzle(gold).public_task()["fen"]}
    rows = [{**row, "puzzle_id": f"synthetic-{i}"} for i in range(12)]
    monkeypatch.setattr(dataset._SPEC, "_rows", lambda: {"test": rows})
    monkeypatch.setattr(dataset._SPEC, "_records", [])
    monkeypatch.setattr(dataset._SPEC, "load_error", None)
    monkeypatch.setattr(dataset, "_BUILT", None)
    tasks, scorable = dataset.load()
    ids = {task["task_id"] for task in tasks}
    # One released puzzle slice: the adapter declares no division, so the whole pool is
    # what a run sees (2026-10-24; the platform's hash third is a run-time choice now).
    assert dataset.SPLIT is None
    assert {task["task_id"] for task in tasks} == ids
    assert set(dataset.ENV) == set(dataset.SETUP) == set(dataset.VERIFY) == ids
    assert set(scorable) == ids
    assert all(set(task) == {"task_id", "goal", "inputs"} for task in tasks)
    assert all("synthetic-" not in task["goal"] for task in tasks)


def test_materialized_verifier_preserves_both_metrics_and_redacts_reference(tmp_path):
    gold = {"fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e4"],
            "puzzle_id": "synthetic-illegal-reference"}
    spec = dataset._SPEC.verifier({"task_id": gold["puzzle_id"], "gold": gold})
    for entry in spec["inputs"]:
        path = tmp_path / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            path.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], path)
    (tmp_path / "answer.txt").write_text("e7e4")
    proc = subprocess.run([sys.executable, *spec["argv"][1:]], cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(tmp_path)},
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (tmp_path / "verifier/reward.txt").read_text().strip() == "0"
    evidence = json.loads((tmp_path / "verifier/evidence.json").read_text())
    assert evidence["measured"] is True
    assert evidence["gold"]["redacted"] is True
    assert "moves" not in evidence["gold"] and "fen" not in evidence["gold"]
    assert evidence["official"]["strict"]["verdict"] == "illegal_move"
    assert evidence["official"]["upstream"]["reward"] == 1.0


def test_shared_qa_rematerialization_contract_with_synthetic_redacted_gold(
    monkeypatch, gold, tmp_path,
):
    monkeypatch.setattr(dataset._SPEC, "_records", [
        {"task_id": "chessbench_puzzles/test/0", "gold": gold}])
    shared = runpy.run_path(str(Path(__file__).with_name("test_qa_dataset_contract.py")))
    shared["test_the_check_re_materializes_the_gold"](dataset, tmp_path)
