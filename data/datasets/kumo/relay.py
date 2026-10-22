#!/usr/bin/env python3
"""Serve one immutable KUMO case outside the agent sandbox; never expose the table."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.service.tool_transport import Relay


class Game:
    def __init__(self, case, digest, budget):
        if budget != case["max_queries"] or case["schema"] != "kumo.case.v1":
            raise ValueError("case/budget mismatch")
        self.case = case
        self.digest = digest
        self.budget = budget
        self.actions = []
        self.rejected = 0
        self.lock = threading.Lock()

    def handle(self, op, args):
        with self.lock:
            if op == "stats":
                return {"queries_used": len(self.actions), "max_queries": self.budget}
            if op != "do" or not isinstance(args.get("action"), str):
                self.rejected += 1
                return {"accepted": False, "error": "invalid_request"}
            action = args["action"].strip()
            if action not in self.case["actions"]:
                self.rejected += 1
                return {"accepted": False, "error": "invalid_action"}
            if len(self.actions) >= self.budget:
                self.rejected += 1
                return {"accepted": False, "error": "query_budget_exhausted"}
            entry = {"step": len(self.actions) + 1, "action": action,
                     "observation": self.case["observations"][action]}
            self.actions.append(entry)
            return {"accepted": True, **entry, "queries_left": self.budget - len(self.actions)}

    def receipt(self, transport, clean):
        return {"schema": "kumo.episode.v1", "protocol": "kumo.query50.v1",
                "task_id": self.case["task_id"], "case_sha256": self.digest,
                "max_queries": self.budget, "queries_used": len(self.actions),
                "actions": self.actions, "rejected": self.rejected,
                "stopped_cleanly": clean, "transport": transport}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-spec", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=50)
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--deadline-s", type=float, default=None)
    parser.add_argument("--max-line-bytes", type=int, default=None)
    args = parser.parse_args()
    spec = json.loads(args.case_spec.read_text())
    raw = Path(spec["path"]).read_bytes()
    case = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != spec["sha256"] or case["task_id"] != spec["task_id"]:
        raise ValueError("KUMO private input digest mismatch")
    game = Game(case, digest, args.budget)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with Relay(args.socket, game.handle, max_calls=args.max_calls, deadline_s=args.deadline_s,
               max_line_bytes=args.max_line_bytes) as relay:
        clean = stop.wait(args.deadline_s)
    args.receipt.write_text(json.dumps(game.receipt(relay.receipt, clean), ensure_ascii=False) + "\n")
    return 0 if clean and not (relay.receipt["errors"] or relay.receipt["refused"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
