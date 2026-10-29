#!/usr/bin/env python3
"""Private selected-episode service; the actor can only next/ack/submit."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets.personamem_v2.lib.protocol import Session
from eval.service.tool_transport import Relay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("episode", "socket", "receipt"):
        parser.add_argument("--" + flag, required=True, type=Path)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-line-bytes", type=int)
    parser.add_argument("--deadline-s", type=float)
    args = parser.parse_args()
    session = Session(json.loads(args.episode.read_text()))
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    with Relay(args.socket, session.handle, max_calls=args.max_calls,
               deadline_s=args.deadline_s, max_line_bytes=args.max_line_bytes) as relay:
        clean = stopped.wait(args.deadline_s)
    Path(os.environ["HG_RELAY_RECORD_PATH"]).write_text(json.dumps(session.record(), ensure_ascii=False))
    args.receipt.write_text(json.dumps({
        "schema": "personamem-v2-relay-receipt-v1", "task_id": session.payload["task_id"],
        "stopped_cleanly": clean, "protocol_rejections": session.rejected, "transport": relay.receipt}))
    # Malformed actor envelopes never reach the session and can be corrected.
    # Handler/runtime errors and resource refusals still invalidate the service.
    service_failed = (relay.receipt["errors"] > relay.receipt["request_errors"]
                      or relay.receipt["refused"])
    return 0 if clean and not service_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
