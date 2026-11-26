#!/usr/bin/env python3
"""Serve one pinned PTC-Bench case and seal its state outside the actor."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

assets = Path(__file__).resolve().parent
sys.path.insert(0, str(assets))
sys.path.insert(0, str(assets.parents[2] / "eval/service"))

from episode import Episode
from source import bootstrap, verified_case
from tool_transport import Relay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("case-spec", "socket", "receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-line-bytes", type=int)
    parser.add_argument("--deadline-s", type=float)
    args = parser.parse_args()
    spec = json.loads(args.case_spec.read_bytes())
    config = verified_case(spec["case"], spec["sha256"])
    context = json.loads(Path(os.environ["HG_RELAY_CONTEXT_PATH"]).read_bytes())
    if context["task_id"] != config["task_id"]:
        raise ValueError("PTC-Bench relay task binding mismatch")
    bootstrap(spec["bundle"])
    episode = Episode(config["task"])
    stop = threading.Event()
    lock = threading.Lock()
    for signo in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signo, lambda *_: stop.set())

    def handle(op, request):
        with lock:
            return episode.handle(op, request)

    with Relay(args.socket, handle, deadline_s=args.deadline_s,
               max_calls=args.max_calls, max_line_bytes=args.max_line_bytes) as relay:
        clean = stop.wait(args.deadline_s)
        with lock:
            record = {**episode.snapshot(), "task_id": config["task_id"], "case_sha256": spec["sha256"]}
            Path(os.environ["HG_RELAY_RECORD_PATH"]).write_text(
                json.dumps(record, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8")
    args.receipt.write_text(json.dumps({"transport": relay.receipt, "stopped_cleanly": clean}),
                            encoding="utf-8")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
