#!/usr/bin/env python3
"""A scripted OpenAI endpoint, so miniswe can be measured with no model and no network.

Every other base harness here has a `mock` backend, and it exists for a specific reason:
`tools/validate_harness.py --smoke` runs a harness on a trivial task with **no model
configured**, and a harness that cannot answer that must still prove it writes an answer
file. `loop` does it with a python function; miniswe cannot, because it is a third-party
CLI that speaks HTTP, not `HG_AGENT_*`.

So the same idea one level down: a real chat-completions server on an ephemeral loopback
port, inside this process, answering from a two-line script. What the mock says is
deliberately useless as an answer -- it writes the literal `MINISWE_MOCK` -- so a run that
somehow ends up on the mock cannot score by accident. What it proves is everything else:
the argv, the environment, the per-task cwd, the tool-call round trip, the trajectory, the
trace and the answer file.

The platform records `agent_backend: "mock"` in `harness_identity`, and a mock run's score
is not a measurement of anything (`HG_AGENT_BACKEND=mock` is the platform's own default,
and every dataset that reads it says so).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: What the mock does, in order, one bash command per model reply. The first writes a file
#: so the answer-file contract is exercised; the second is miniswe's own submit command.
SCRIPT = (
    "printf 'MINISWE_MOCK\\n' > answer.txt",
    "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):            # the test output is the report, not this
        pass

    def do_GET(self):
        self._json({"object": "list", "data": []})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        # miniswe's instance prompt is a `user` message; every observation after it is a
        # `tool` message. Counting the observations is what picks the next step -- counting
        # `user` messages would make the first request look like the second step, and the
        # agent would submit before doing anything.
        steps = sum(1 for m in body.get("messages") or [] if m.get("role") == "tool")
        command = SCRIPT[min(steps, len(SCRIPT) - 1)]
        self._json({
            "id": "mock-1", "object": "chat.completion", "created": 0,
            "model": body.get("model") or "mock",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": f"mock_{steps}", "type": "function", "function": {
                    "name": "bash", "arguments": json.dumps({"command": command})}}]}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start() -> tuple[str, ThreadingHTTPServer]:
    """`(base_url, server)`; the caller shuts it down. Port 0, so two runs never collide."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}/v1", server
