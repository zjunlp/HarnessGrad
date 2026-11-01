"""A model-free OpenAI-compatible SSE endpoint for the native OpenClaw CLI."""
from __future__ import annotations
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_args):
        pass
    def do_GET(self):
        self._json({"object": "list", "data": []})
    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except (ValueError, TypeError):
            self.send_error(400)
            return
        self.server.requests.append(body)
        model = body.get("model") or "openclaw-mock"
        has_tool_result = any(m.get("role") == "tool" for m in body.get("messages") or [])
        chunks = [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
        if has_tool_result:
            chunks += [{"index": 0, "delta": {"content": "OPENCLAW_MOCK"}, "finish_reason": None},
                       {"index": 0, "delta": {}, "finish_reason": "stop"}]
        else:
            args = json.dumps({"command": "printf 'OPENCLAW_MOCK\\n' > answer.txt", "yieldMs": 10000})
            chunks += [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "mock_exec",
                       "type": "function", "function": {"name": "exec", "arguments": args}}]},
                       "finish_reason": None}, {"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
        lines = []
        for chunk in chunks:
            lines.append("data: " + json.dumps({"id": "mock-response", "object": "chat.completion.chunk",
                "created": 0, "model": model, "choices": [chunk]}))
        lines.append("data: " + json.dumps({"id": "mock-response", "object": "chat.completion.chunk",
            "created": 0, "model": model, "choices": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}))
        lines.append("data: [DONE]")
        encoded = ("\n\n".join(lines) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
    def _json(self, payload):
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

def start():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}/v1", server
