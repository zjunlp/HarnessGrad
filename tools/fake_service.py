#!/usr/bin/env python3
"""A mock order service, for `data/verify_service.py` and the service tests.

Standard library only, on purpose: the demo image is then a stock `python:3.11-slim`
plus this one file, which is what makes an end-to-end service test cheap enough to run
inside the normal suite.

It exists to exercise the three things about a service that the platform has to get
right and that nothing else can demonstrate:

* **It is not ready the instant it starts listening.** It binds the socket first and
  only starts answering `/health` a moment later, so a platform that skipped the
  health check would race it. That delay is the whole reason `health` is a required
  field rather than a default of "is the port open" (INTERFACE.md §2.5.7).
* **It records what it was sent**, into `/recordings/orders/harnessgrad.json`. That
  file is the only evidence `s02` and `s03` are graded on, and it is mounted read-only
  into the check and **not at all** into the harness — so a harness can neither write
  the evidence nor read it.
* **It reports no `usage`**, because it calls no model. That exercises the honest
  "not reported" path: `cost.env_generation_tokens` must be `null` rather than `0`, in
  the same way a harness that reports no token usage gets `null` rather than zero.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Where the platform mounted this service's recording. The path inside the container
#: is fixed by the platform (`eval/services.RECORDINGS_ROOT` + the service name); the
#: environment variable exists so the file can also be run by hand.
RECORD_DIR = pathlib.Path(os.environ.get("HG_RECORD_DIR", "/recordings/orders"))

#: How long to stay unready after the socket is up. Measured in a second, not
#: simulated: a harness that starts against a service in this window gets a connection
#: refused, which is precisely the race the health check exists to remove.
STARTUP_DELAY_S = float(os.environ.get("HG_STARTUP_DELAY_S", "1.5"))

STATE = {"requests": 0, "order_count": 0, "received": [], "ready": False}
LOCK = threading.Lock()


def flush() -> None:
    """Write the recording. Called under the lock, after every request.

    Written on every request rather than at shutdown because the check runs while this
    process is still alive — a service that only writes its evidence on exit would be
    a service whose evidence does not exist yet at the moment it is needed.
    """
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "requests": STATE["requests"],
        "order_count": STATE["order_count"],
        "received": STATE["received"],
        # No `usage` key: this service calls no model, and the platform must record
        # that as "nothing was reported" rather than as a spend of zero.
    }
    tmp = RECORD_DIR / "harnessgrad.json.tmp"
    tmp.write_text(json.dumps(payload))
    # Renamed into place so the check never reads a half-written file: it runs
    # concurrently with this process, which is a property unique to a recording.
    tmp.replace(RECORD_DIR / "harnessgrad.json")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - the name is the protocol
        if self.path == "/health":
            # 503 until the delay has elapsed: the difference between "listening" and
            # "ready" is the thing this service is here to represent.
            #
            # `/health` is deliberately **never recorded**. The health check is
            # infrastructure, not interaction, and a check that graded "did the harness
            # call the service" by counting requests would be satisfied by the
            # platform's own probe -- a check that cannot fail. Measured: an earlier
            # version recorded it, and `s01` passed for every harness including one
            # that never made a call.
            if not STATE["ready"]:
                return self._reply(503, {"ok": False, "why": "still starting"})
            return self._reply(200, {"ok": True})
        with LOCK:
            STATE["requests"] += 1
            STATE["received"].append({"path": self.path, "method": "GET", "body": {}})
            flush()
        if self.path == "/orders":
            return self._reply(200, {"order_count": STATE["order_count"]})
        self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"unparseable": raw.decode("utf-8", "replace")[:200]}
        with LOCK:
            STATE["requests"] += 1
            STATE["received"].append({"path": self.path, "method": "POST",
                                      "body": body})
            if self.path == "/order":
                STATE["order_count"] += 1
            flush()
            count = STATE["order_count"]
        self._reply(200, {"ok": True, "order_count": count})

    def log_message(self, *args) -> None:
        """Silent: the recording is the record, and stderr goes nowhere useful."""


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    # Ready only after the delay, and announced on stdout so a human running this by
    # hand can see which state it is in.
    def become_ready() -> None:
        time.sleep(STARTUP_DELAY_S)
        STATE["ready"] = True
        print(f"ready after {STARTUP_DELAY_S}s", flush=True)

    threading.Thread(target=become_ready, daemon=True).start()
    print("listening on 8080 (not yet healthy)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
