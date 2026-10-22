"""Apache-2.0 upstream scorer, verbatim, with differential and source-drift tests.

Google DeepMind searchless_chess@90ae0e6b121673fc3079aaeffa047580bb600c0a,
src/puzzles.py lines 74-94. Type aliases stand in for the upstream engine package only;
the function text below is unchanged. No neural engine or training is involved.
"""
from __future__ import annotations

from collections.abc import Sequence
import csv
import inspect
import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import chess
import chess.pgn
import pytest

import data.datasets.chessbench_puzzles as dataset

engine_lib = SimpleNamespace(Engine=object)
SOURCE = (dataset.DATASET_ROOT / "_src" /
          f"searchless_chess-{dataset.REVISION}" / "src" / "puzzles.py")


# Copyright 2025 DeepMind Technologies Limited. Apache License, Version 2.0.
def evaluate_puzzle_from_board(
    board: chess.Board,
    moves: Sequence[str],
    engine: engine_lib.Engine,
) -> bool:
  """Returns True if the `engine` solves the puzzle and False otherwise."""
  for move_idx, move in enumerate(moves):
    # According to https://database.lichess.org/#puzzles, the FEN is the
    # position before the opponent makes their move. The position to present to
    # the player is after applying the first move to that FEN. The second move
    # is the beginning of the solution.
    if move_idx % 2 == 1:
      predicted_move = engine.play(board=board).uci()
      # Lichess puzzles consider all mate-in-1 moves as correct, so we need to
      # check if the `predicted_move` results in a checkmate if it differs from
      # the solution.
      if move != predicted_move:
        board.push(chess.Move.from_uci(predicted_move))
        return board.is_checkmate()
    board.push(chess.Move.from_uci(move))
  return True


class SequenceEngine:
    def __init__(self, moves):
        self.moves = iter(moves)

    def play(self, *, board):
        return chess.Move.from_uci(next(self.moves))


def reference(fen, moves, predictions):
    try:
        return evaluate_puzzle_from_board(chess.Board(fen), moves, SequenceEngine(predictions))
    except (StopIteration, ValueError, AssertionError):
        return False


@pytest.mark.parametrize("fen,moves,predictions", [
    (chess.STARTING_FEN, ["e2e4", "e7e5", "g1f3", "b8c6"], ["e7e5", "b8c6"]),
    (chess.STARTING_FEN, ["e2e4", "e7e5", "g1f3", "b8c6"], ["e7e5"]),
    (chess.STARTING_FEN, ["e2e4", "e7e5"], ["b8c6"]),
    (chess.STARTING_FEN, ["e2e4", "e7e5"], ["0000"]),
    (chess.STARTING_FEN, ["e2e4", "e7e4"], ["e7e4"]),
    ("7k/5K2/6Q1/8/p7/8/8/8 b - - 0 1", ["a4a3", "g6g7"], ["g6g8"]),
    ("7k/5K2/6Q1/8/p7/8/8/8 b - - 0 1",
     ["a4a3", "g6g7", "h8h7", "g7h7"], ["g6g7"]),
])
def test_synthetic_upstream_diagnostic_matches_verbatim(fen, moves, predictions):
    gold = {"fen": fen, "moves": moves, "puzzle_id": "synthetic"}
    result = dataset.score(" ".join(predictions), gold)
    assert result["official"]["upstream"]["correct"] == reference(fen, moves, predictions)


def test_verbatim_upstream_function_has_not_drifted():
    assert SOURCE.is_file(), "fetch the declared pinned scorer source archive"
    upstream = "\n".join(SOURCE.read_text().splitlines()[73:94]) + "\n"
    assert inspect.getsource(evaluate_puzzle_from_board) == upstream


def test_every_released_gold_replays_to_one_and_noop_to_zero():
    with dataset.CSV_PATH.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 10000
    assert len({r["PuzzleId"] for r in rows}) == 10000
    for row in rows:
        game = chess.pgn.read_game(io.StringIO(row["PGN"]))
        assert game is not None
        assert game.end().board().fen() == row["FEN"]
        moves = row["Moves"].split()
        board = chess.Board(row["FEN"])
        for uci in moves:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves, (row["PuzzleId"], uci)
            board.push(move)
        gold = {"fen": row["FEN"], "moves": moves, "puzzle_id": row["PuzzleId"]}
        answer = moves[1::2]
        assert reference(row["FEN"], moves, answer)
        assert dataset.score(" ".join(answer), gold)["reward"] == 1.0
        assert dataset.score("", gold)["reward"] == 0.0


@pytest.mark.parametrize("case", ["gold", "short", "setup_included", "junk", "extra", "wrong_legal"])
def test_differential_predictions(case):
    records = dataset._RECORDS
    for record in records[::499]:
        gold = record["gold"]
        predicted = gold["moves"][1::2]
        if case == "short":
            predicted = predicted[:-1]
        elif case == "setup_included":
            predicted = gold["moves"]
        elif case == "junk":
            predicted = ["not-uci"]
        elif case == "extra":
            predicted = predicted + ["a1a2"]
        elif case == "wrong_legal":
            board = chess.Board(gold["fen"])
            board.push_uci(gold["moves"][0])
            predicted = [next(m.uci() for m in board.legal_moves if m.uci() != predicted[0])]
        expected = reference(gold["fen"], gold["moves"], predicted)
        result = dataset.score(" ".join(predicted), gold)
        assert result["official"]["upstream"]["correct"] == expected


def test_alternate_mate_and_nonmate_against_official_function():
    # Entirely synthetic: KQ vs K with a pawn setup has multiple immediate mates.
    fen = "7k/5K2/6Q1/8/p7/8/8/8 b - - 0 1"
    board = chess.Board(fen)
    setup = chess.Move.from_uci("a4a3")
    board.push(setup)
    mates, nonmates = [], []
    for move in board.legal_moves:
        child = board.copy()
        child.push(move)
        (mates if child.is_checkmate() else nonmates).append(move.uci())
    assert len(mates) >= 2 and nonmates
    moves = [setup.uci(), mates[0]]
    gold = {"fen": fen, "moves": moves, "puzzle_id": "synthetic"}
    assert reference(fen, moves, [mates[1]])
    assert dataset.score(mates[1], gold)["reward"] == 1.0
    assert not reference(fen, moves, [nonmates[0]])
    assert dataset.score(nonmates[0], gold)["reward"] == 0.0


@pytest.mark.parametrize("arm,expected", [("--oracle", "1.0"), ("--noop", "0.0")])
def test_platform_control(arm, expected):
    proc = subprocess.run(
        [sys.executable, "data/scaffolds/qa/control.py", "--dataset", "chessbench_puzzles",
         arm, "--platform"], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"score    {expected}" in proc.stdout
    assert "sandbox=True" in proc.stdout
