"""ChessBench's 10,000-puzzle tactical slice.

Source: `google-deepmind/searchless_chess`, ChessBench paper (NeurIPS 2024,
arXiv:2402.04494), `puzzles.csv`.

Official puzzle accuracy uses interactive stepwise play: after each solver move the
trusted evaluator applies the reference opponent response and presents the updated board.
`StepwisePuzzle` implements that public board / single-UCI-answer contract without exposing
the reference line. It belongs in a trusted controller, never in the actor's filesystem.

The existing QA task still accepts a space-separated sequence. Its reward is explicitly
the offline strict replay variant, not interactive official accuracy or Elo. Every applied
move is checked for legality, including setup and reference opponent moves. Any legal
immediate mate succeeds and ends play; unused trailing answers are ignored. The pinned
upstream replay, including its unchecked Board.push behavior, is retained separately as a
diagnostic in `official.upstream`; it does not determine reward. Importing this module
does not read the puzzle CSV.
"""
from __future__ import annotations

import csv
from pathlib import Path
import re

import chess

from data.scaffolds.qa import base

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"
# The shared verifier otherwise includes the complete private reference in evidence.
REDACT_EVIDENCE = True
DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/chessbench_puzzles")
CSV_PATH = DATASET_ROOT / "_src" / "puzzles.csv"
CSV_MEMBER = "puzzles.csv"
REVISION = "90ae0e6b121673fc3079aaeffa047580bb600c0a"
CSV_SHA256 = "099d54e681a07b9e82b4462aaf5436a6e833edc6f56b92ea7c2d2413f7efbe48"
STRICT_METRIC = "ChessBench strict legal puzzle replay v1"
UPSTREAM_METRIC = "ChessBench upstream puzzle replay (unchecked push)"
STEPWISE_CONTRACT = "chessbench_puzzles.stepwise.v1"
_TASK_ID_RE = re.compile(r"chessbench_puzzles/test/(0|[1-9][0-9]*)")


def _read_rows() -> list[dict]:
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 10000:
        raise ValueError(f"ChessBench expected 10000 rows, got {len(rows)}")
    return rows


def _presented_fen(row: dict) -> str:
    moves = row["Moves"].split()
    if not moves:
        raise ValueError(f"{row['PuzzleId']}: empty Moves")
    board = chess.Board(row["FEN"])
    board.push_uci(moves[0])
    return board.fen()


def _row(row: dict) -> dict:
    return {
        "puzzle_id": row["PuzzleId"],
        "rating": int(row["Rating"]),
        "fen": row["FEN"],
        "presented_fen": _presented_fen(row),
        "moves": row["Moves"].split(),
    }


def _rows() -> dict[str, list[dict]]:
    return {"test": [_row(row) for row in _read_rows()]}


def _selected_records(task_ids: list[str]) -> list[dict]:
    """Read through the last selected CSV index; decode only selected puzzles.

    Validate the entire selection before opening the CSV. Keep this separate from
    QASpec's full-pool cache so a targeted control cannot change later pool loading.
    """
    ids = list(dict.fromkeys(task_ids))
    if not ids:
        raise ValueError("ChessBench selection must not be empty")
    indices = {}
    for task_id in ids:
        match = _TASK_ID_RE.fullmatch(task_id)
        if match is None or int(match[1]) >= 10000:
            raise ValueError(f"unknown ChessBench task id {task_id!r}")
        indices[int(match[1])] = task_id
    remaining = set(indices)
    records = {}
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        for index, raw in enumerate(csv.DictReader(handle)):
            if index not in remaining:
                continue
            row = _row(raw)
            task_id = indices[index]
            records[task_id] = {
                "task_id": task_id,
                "index": index,
                "split": "test",
                "row": row,
                "question": _question(row),
                "gold": _gold(row),
            }
            remaining.remove(index)
            if not remaining:
                break
    if remaining:
        raise ValueError(f"ChessBench CSV is missing selected indices {sorted(remaining)}")
    return [records[task_id] for task_id in ids]


def _question(row: dict) -> str:
    return (
        "Solve this ChessBench tactical puzzle using the offline strict sequence variant "
        "(not interactive official puzzle accuracy). The opponent's setup move has already "
        "been applied.\n\n"
        f"Position (FEN): {row['presented_fen']}\n\n"
        "Submit the solver's moves as space-separated UCI moves, for example "
        "`e2e4 g1f3`. Do not include opponent moves. Every applied move must be legal; "
        "a legal immediate checkmate ends the puzzle. Unused trailing tokens are ignored."
    )


def _gold(row: dict) -> dict:
    return {
        "answer": " ".join(row["moves"][1::2]),
        "fen": row["fen"],
        "moves": row["moves"],
        "puzzle_id": row["puzzle_id"],
    }


def _goal(question: str, gold: dict) -> str:
    return (
        f"{question}\n\n"
        "Write the UCI move sequence alone to `answer.txt`; the qa submit command is "
        "`python3 qa/bin/qa submit \"<moves>\"`. "
        "Only solver moves belong in the answer. This batch interface supplies no "
        "intermediate observations; stepwise evaluation requires the trusted "
        "`chessbench_puzzles.stepwise.v1` controller contract."
    )


def _official_replay(board: chess.Board, moves: list[str], predicted: list[str]) -> bool:
    """The official `evaluate_puzzle_from_board` loop, with engine output supplied by us.

    Upstream source: `src/puzzles.py`, lines 74-94 at the pinned repository revision.
    `predicted` replaces `engine.play(board).uci()` so the dataset answer contract can carry
    a complete scripted sequence instead of a live engine object.
    """
    solver_index = 0
    for move_idx, move in enumerate(moves):
        # According to the Lichess puzzle format, index 0 is the opponent setup move and
        # index 1 is the first move presented to the player.
        if move_idx % 2 == 1:
            if solver_index >= len(predicted):
                return False
            predicted_move = predicted[solver_index]
            solver_index += 1
            if move != predicted_move:
                try:
                    board.push(chess.Move.from_uci(predicted_move))
                except ValueError:
                    return False
                # Lichess accepts any mate-in-one when it differs from the stored line.
                return board.is_checkmate()
            try:
                board.push(chess.Move.from_uci(predicted_move))
            except ValueError:
                return False
        else:
            try:
                board.push(chess.Move.from_uci(move))
            except ValueError:
                return False
    # Like an Engine, only requested moves count; trailing scripted moves are unused.
    return True


def score_upstream(prediction: str, gold: dict) -> dict:
    """Diagnostic only: preserve the pinned upstream loop's unchecked-push semantics."""
    try:
        moves = list(gold["moves"])
        hit = bool(moves) and _official_replay(
            chess.Board(gold["fen"]), moves, prediction.strip().split())
    except (ValueError, AssertionError):
        hit = False
    return {
        "metric": UPSTREAM_METRIC,
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
        "protocol": "offline sequence; not interactive official accuracy",
    }


class StepwisePuzzle:
    """Trusted session; only `public_task()` results may cross into the actor.

    Construct with verifier-owned gold. Present `public_task()`; receive exactly
    {"uci": "e2e4"}; call `submit()` and present its result before asking again.
    Responses reveal the resulting board, turn number and verdict, never the expected
    solver move, future opponent response, line length, original FEN or reference line.
    A runner must isolate this object and gold from the actor, and retain submissions
    itself; an actor-authored transcript is not evidence of interactive evaluation.
    """

    def __init__(self, gold: dict):
        self._board = None
        self._moves = list(gold["moves"])
        self._index = 1
        self._solver_turn = 0
        self._status = "awaiting_move"
        self._verdict = "awaiting_move"
        try:
            self._board = chess.Board(gold["fen"])
        except ValueError:
            self._finish("invalid_reference")
            return
        if not self._board.is_valid() or len(self._moves) < 2:
            self._finish("invalid_reference")
            return
        move = self._legal_move(self._moves[0], reference=True)
        if move is not None:
            self._board.push(move)
            if self._board.is_game_over():
                self._finish("invalid_reference")

    def _finish(self, verdict: str) -> None:
        self._verdict = verdict
        self._status = "solved" if verdict in {
            "reference_complete", "checkmate", "alternate_mate_in_one"} else "failed"

    def _legal_move(self, uci: str, *, reference: bool = False):
        try:
            move = chess.Move.from_uci(uci)
        except (ValueError, TypeError):
            self._finish("invalid_reference" if reference else "invalid_move")
            return None
        # Board.push() is deliberately unchecked in python-chess, even for null moves.
        if move not in self._board.legal_moves:
            self._finish("invalid_reference" if reference else "illegal_move")
            return None
        return move

    def public_task(self) -> dict:
        return {
            "contract": STEPWISE_CONTRACT,
            "fen": self._board.fen() if self._board is not None else None,
            "solver_turn": self._solver_turn,
            "status": self._status,
            "verdict": self._verdict,
        }

    def submit(self, answer: dict) -> dict:
        if self._status != "awaiting_move":
            raise ValueError("puzzle session is already terminal")
        if (not isinstance(answer, dict) or set(answer) != {"uci"}
                or not isinstance(answer["uci"], str)):
            self._finish("invalid_answer")
            return self.public_task()
        move = self._legal_move(answer["uci"])
        if move is None:
            return self.public_task()
        expected = self._moves[self._index]
        self._board.push(move)
        self._solver_turn += 1
        if self._board.is_checkmate():
            self._finish("checkmate" if answer["uci"] == expected
                         else "alternate_mate_in_one")
        elif answer["uci"] != expected:
            self._finish("wrong_move")
        else:
            self._index += 1
            if self._index < len(self._moves):
                reply = self._legal_move(self._moves[self._index], reference=True)
                if reply is None:
                    return self.public_task()
                self._board.push(reply)
                self._index += 1
            if self._index == len(self._moves):
                self._finish("reference_complete")
            elif self._board.is_game_over():
                self._finish("invalid_reference")
        return self.public_task()

    def result(self) -> dict:
        hit = self._status == "solved"
        return {"metric": STRICT_METRIC, "reward": float(hit), "correct": hit,
                "verdict": self._verdict, "solver_moves_played": self._solver_turn}


def score_strict(prediction: str, gold: dict) -> dict:
    """Replay requested solver tokens through the same legal stepwise state machine."""
    session = StepwisePuzzle(gold)
    for token in prediction.strip().split():
        if session.public_task()["status"] != "awaiting_move":
            break
        session.submit({"uci": token})
    if session.public_task()["status"] == "awaiting_move":
        session._finish("missing_move")
    return session.result()


def score(prediction: str, gold: dict) -> dict:
    strict = score_strict(prediction, gold)
    upstream = score_upstream(prediction, gold)
    return {
        **strict,
        "official": {
            "metric": STRICT_METRIC,
            "protocol": "offline strict sequence; not interactive official accuracy",
            "puzzle_id": gold["puzzle_id"],
            "strict": strict,
            "upstream": upstream,
        },
        "why": f"strict legal replay: {strict['verdict']}",
    }


def downloads() -> list[dict]:
    return [
        {
            "kind": "http",
            "name": "ChessBench puzzles.csv",
            "version": REVISION,
            "url": "https://storage.googleapis.com/searchless_chess/data/puzzles.csv",
            "size": "4,705,735 bytes",
            "sha256": CSV_SHA256,
            "dst": "_src/puzzles.csv",
            "note": "Canonical ChessBench slice. Trap: the first Moves token is the opponent setup move; the scorer predicts only odd-indexed solver moves and accepts alternate immediate mates.",
        },
        {
            "kind": "http",
            "name": "ChessBench official scorer source",
            "version": REVISION,
            "url": "https://codeload.github.com/google-deepmind/searchless_chess/tar.gz/" + REVISION,
            "size": "~2.2 MB compressed",
            "sha256": "9cf0832b04024a9e0a6edb59f0dee62aeadee3b9b97799b9a7521b2221f7d39a",
            "dst": "_src/searchless_chess.tar.gz",
            "extract_to": "_src",
            "note": "Pins src/puzzles.py lines 74-94 for the verbatim scorer and drift check. Fetch this small source archive, not the terabyte-scale ChessBench training corpus.",
        },
        {
            "kind": "pip",
            "name": "python-chess",
            "version": "1.11.2",
            "package": "chess",
            "size": "~148 KB wheel",
            "note": "Parses FEN and UCI moves. Pin this dependency because strict replay checks legal_moves before push; upstream push itself does not enforce legality.",
        },
    ]


def provision_status() -> dict:
    return {
        "root": str(DATASET_ROOT),
        "present": CSV_PATH.is_file() and CSV_PATH.stat().st_size == 4705735,
        "split_dir": str(DATASET_ROOT),
        "files": [str(CSV_PATH)],
        "present_files": [str(CSV_PATH)] if CSV_PATH.is_file() else [],
        "tasks": 10000,
    }


_SPEC = base.QASpec(
    name="chessbench_puzzles",
    root=DATASET_ROOT,
    rows=_rows,
    question=_question,
    gold=_gold,
    goal=_goal,
    score=score,
    downloads=downloads,
    split="test",
    source="google-deepmind/searchless_chess@" + REVISION + " data/puzzles.csv",
)

# QASpec.build() reads the whole CSV. Defer it until pool metadata is explicitly needed.
_BUILT = None
DEFAULT_ENV = {"kind": "files"}

#: The benchmark's own evaluation set, within this adapter's pool. The pool is the whole released `puzzles.csv` (10,000), which is the
#: benchmark's puzzle slice.
#:
#: `None`, matching `QASpec.division()`, because the benchmark released no study split for this
#: table: the pool **is** the scored split, so every task in it belongs to the
#: evaluation set, and that claims nothing about whether the pool is the whole of it.
#: `--split official --side eval` selects the whole pool; it does not make this batch
#: interface equivalent to interactive official accuracy.
#: `--split declared` is the same whole pool, because there is no division to declare.
OFFICIAL_EVAL = None

#: `"none"`: the benchmark states no division, so this adapter declares none and the run
#: covers every task. It used to cut its own pool with a hash third (`"hash"`), an invented
#: division the operator removed on 2026-10-24; `--split default` still names that rule at
#: run time.
SPLIT_SOURCE = "none"


def __getattr__(name: str):
    global _BUILT
    if name == "VERIFY":
        return {record["task_id"]: verify_for(record) for record in _SPEC.ensure()}
    if name in {"ENV", "SPLIT"}:
        if _BUILT is None:
            _BUILT = _SPEC.build()
        return _BUILT[name]
    return base.module_getattr(_SPEC, name)


def load() -> tuple[list[dict], dict[str, str]]:
    return _SPEC.load()


def load_selected(task_ids: list[str]) -> tuple[list[dict], dict[str, str]]:
    """Load exactly the requested tasks without constructing pool metadata."""
    records = _selected_records(task_ids)
    return ([{"task_id": record["task_id"],
              "goal": _goal(record["question"], record["gold"]), "inputs": []}
             for record in records],
            {record["task_id"]: record["gold"]["answer"] for record in records})


def control_task(task_id: str | None) -> dict:
    """Get one trusted control record; the default eval id needs no pool read."""
    if task_id is None:
        task_id = next(f"chessbench_puzzles/test/{index}" for index in range(10000)
                       if base.is_eval(f"chessbench_puzzles/test/{index}"))
    return _selected_records([task_id])[0]


def setup_for(record: dict) -> dict:
    return _SPEC.setup(record)


def verify_for(record: dict) -> dict:
    from data.runtimes import NATIVE_PINS
    spec = _SPEC.verifier(record)
    spec['native_runtime'] = NATIVE_PINS['chessbench_puzzles'].copy()
    return spec


__all__ = ["KIND", "DATASET_ROOT", "REVISION", "load", "load_selected", "control_task",
           "setup_for", "verify_for", "downloads", "score", "SPLIT", "ENV",
           "score_strict", "score_upstream", "StepwisePuzzle", "STEPWISE_CONTRACT"]
