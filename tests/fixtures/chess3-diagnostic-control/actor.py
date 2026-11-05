"""Diagnostic protocol control, NOT a candidate chess method.

Choose the lexicographically first legal UCI move from each public FEN. No
model, engine, search, dataset access or puzzle-specific strategy is used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket

import chess

CONTRACT = "chessbench_puzzles.stepwise.v1"
PUBLIC_FIELDS = {"contract", "fen", "solver_turn", "status", "verdict"}
SOCKET_TIMEOUT_S = 300
MAX_MESSAGE_BYTES = 4096


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    root = Path(args.workdir)

    def trace(event, **fields):
        with (root / "trace.jsonl").open("a") as handle:
            handle.write(json.dumps(dict(event=event, **fields)) + "\n")

    socket_path = os.environ["HG_STEPWISE_SOCKET"]
    trace("control", diagnostic_control=True, candidate_method=False,
          move_rule="lexicographically first legal UCI", model_used=False,
          endpoint=None, socket=socket_path, task_path=args.task,
          python_chess_version=chess.__version__,
          socket_timeout_s=SOCKET_TIMEOUT_S, max_message_bytes=MAX_MESSAGE_BYTES)
    # One descriptor, retained from the initial board through terminal delivery.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(SOCKET_TIMEOUT_S)
        channel.connect(socket_path)
        with channel.makefile("rb") as reader:
            while True:
                wire = reader.readline(MAX_MESSAGE_BYTES + 2)
                if not wire.endswith(b"\n") or len(wire) - 1 > MAX_MESSAGE_BYTES:
                    raise ValueError("missing or oversized observation frame")
                observation = json.loads(wire)
                if (set(observation) != PUBLIC_FIELDS
                        or observation["contract"] != CONTRACT
                        or type(observation["solver_turn"]) is not int
                        or observation["solver_turn"] < 0):
                    raise ValueError("unexpected public observation")
                trace("observation", observation=observation)
                if observation["status"] in {"solved", "failed"}:
                    trace("terminal_received", status=observation["status"],
                          verdict=observation["verdict"])
                    break
                if observation["status"] != "awaiting_move":
                    raise ValueError("unexpected observation status")
                # solver_turn is a zero-based counter, never a readiness boolean.
                board = chess.Board(observation["fen"])
                move = min(move.uci() for move in board.legal_moves)
                answer = {"uci": move}
                channel.sendall(json.dumps(answer).encode("utf-8") + b"\n")
                trace("submission", solver_turn=observation["solver_turn"], answer=answer)
    trace("completed", terminal_received=True, channel_closed=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
