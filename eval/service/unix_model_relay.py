"""Agent-only HTTP over AF_UNIX, with a fixed localhost:8002 model route.

Budgets reserve attempted calls and requested output tokens before forwarding.
Failed/unknown requests retain their reservation. No credentials or caller headers
cross this boundary. Input is bounded in bytes; tokenizer-level input cost is not
claimed. Production target has no CLI override; _connect is a trusted test seam.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

#: **The agent endpoint, not a constant.** A bwrap task reaches the model through this relay,
#: and its route used to be `127.0.0.1:8002` -- one vLLM server, one GPU. A docker task sets
#: `HG_AGENT_BASE_URL` and goes through `lb_proxy`, so the two sandbox kinds were using different
#: amounts of the machine: measured 2026-10-20T15:35Z, four ALFWorld runs at jobs=12 held GPU0 at
#: 0% and GPU1 at 100%, and `8100/stats` counted no traffic at all for them. The default stays
#: 8002 (the endpoint this machine keeps) so a caller that sets nothing sees no change.
_DEFAULT_ENDPOINT = "http://127.0.0.1:8002/v1"


def _route(raw: str) -> "tuple[str, int, str]":
    """`(host, port, path)` from an endpoint URL, accepting a bare `host:port`."""
    from urllib.parse import urlsplit
    parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    path = (parts.path or "/v1").rstrip("/") + "/chat/completions"
    return parts.hostname or "127.0.0.1", parts.port or 8002, path


HOST, PORT, ROUTE = _route(os.environ.get("HG_AGENT_BASE_URL") or _DEFAULT_ENDPOINT)
MAX_CALLS, MAX_TOKENS, MAX_TOTAL_TOKENS = 4, 512, 2048
MAX_BODY, MAX_RESPONSE = 32768, 2097152


def _integer(value, upper):
    return type(value) is int and 0 < value <= upper


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = False
    block_on_close = True

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(5)
        return conn, address


class Relay:
    def __init__(self, directory: Path, *, model: str, max_calls=MAX_CALLS,
                 max_tokens=MAX_TOKENS, max_total_tokens=MAX_TOTAL_TOKENS,
                 _connect=None):
        for value, ceiling in ((max_calls, MAX_CALLS), (max_tokens, MAX_TOKENS),
                               (max_total_tokens, MAX_TOTAL_TOKENS)):
            if not _integer(value, ceiling):
                raise ValueError("relay budget must be positive and within smoke ceilings")
        if not isinstance(model, str) or not model or len(model) > 128:
            raise ValueError("fixed model identity required")
        directory = Path(directory)
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        self.path = directory / "model.sock"
        self.model = model
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.max_total_tokens = max_total_tokens
        self.calls = self.reserved_output_tokens = self.completion_tokens = 0
        self.unknown_usage_calls = 0
        self.rejections = []
        self.blocked = False
        self.lock = threading.Lock()
        self.connect = _connect or http.client.HTTPConnection
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reject(self, status, reason):
                with owner.lock:
                    owner.rejections.append(reason)
                self.reply(status, json.dumps({"error": reason}).encode())

            def reply(self, status, payload):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def do_POST(self):
                if self.path != ROUTE:
                    return self.reject(403, "route")
                lengths = self.headers.get_all("Content-Length", [])
                if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                    return self.reject(400, "framing")
                try:
                    size = int(lengths[0])
                except ValueError:
                    size = 0
                if not 0 < size <= MAX_BODY:
                    return self.reject(413, "body_size")
                try:
                    body = self.rfile.read(size)
                    if len(body) != size:
                        raise ValueError("short body")
                    request = json.loads(body, object_pairs_hook=_unique_object)
                except (ValueError, UnicodeError, TimeoutError, RecursionError):
                    return self.reject(400, "json")
                reason = owner.validate(request)
                if reason:
                    return self.reject(400, reason)
                tokens = request["max_tokens"]
                # Check and reservation are one critical section across all handlers.
                with owner.lock:
                    if owner.blocked or owner.calls >= owner.max_calls:
                        reason = "call_budget"
                    elif owner.reserved_output_tokens + tokens > owner.max_total_tokens:
                        reason = "token_budget"
                    else:
                        reason = None
                        owner.calls += 1
                        owner.reserved_output_tokens += tokens
                if reason:
                    return self.reject(429, reason)
                conn = None
                usage_known = False
                try:
                    conn = owner.connect(HOST, PORT, timeout=10)
                    conn.request("POST", ROUTE, body=json.dumps(request), headers={
                        "Content-Type": "application/json", "Connection": "close"})
                    response = conn.getresponse()
                    payload = response.read(MAX_RESPONSE + 1)
                    if len(payload) > MAX_RESPONSE:
                        raise ValueError("oversized response")
                    decoded = json.loads(payload, object_pairs_hook=_unique_object)
                    if not isinstance(decoded, dict):
                        raise ValueError("provider response object required")
                    usage = decoded.get("usage", {})
                    used = usage.get("completion_tokens") if isinstance(usage, dict) else None
                    if type(used) is int and used >= 0:
                        with owner.lock:
                            owner.completion_tokens += used
                            if used > tokens:
                                owner.blocked = True
                        usage_known = True
                        if used > tokens:
                            return self.reject(502, "provider_token_overrun")
                    # Error bodies are not forwarded: they can contain upstream context.
                    if response.status != 200:
                        return self.reject(502, "provider_error")
                    self.reply(200, payload)
                except (OSError, ValueError, http.client.HTTPException, RecursionError):
                    self.reject(502, "provider_failure")
                finally:
                    if not usage_known:
                        with owner.lock:
                            owner.unknown_usage_calls += 1
                    if conn is not None:
                        conn.close()

        self.server = _Server(str(self.path), Handler)
        os.chmod(self.path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def validate(self, request):
        if not isinstance(request, dict) or set(request) - {
                "model", "messages", "max_tokens", "stream", "n", "temperature"}:
            return "schema"
        if request.get("model") != self.model:
            return "model"
        if request.get("stream", False) is not False or type(request.get("n", 1)) is not int or request.get("n", 1) != 1:
            return "single_nonstream_response_required"
        if not _integer(request.get("max_tokens"), self.max_tokens):
            return "max_tokens"
        temperature = request.get("temperature", 1)
        # Bound integers before isfinite converts them to a float.
        if type(temperature) not in (int, float) or not 0 <= temperature <= 2 or not math.isfinite(temperature):
            return "temperature"
        messages = request.get("messages")
        if not isinstance(messages, list) or not 0 < len(messages) <= 64:
            return "messages"
        for message in messages:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                return "messages"
            if message["role"] not in ("system", "user", "assistant") or not isinstance(message["content"], str):
                return "messages"
        return None

    def receipt(self):
        with self.lock:
            return {"endpoint": f"http://{HOST}:{PORT}{ROUTE}", "model": self.model,
                    "calls": self.calls, "max_calls": self.max_calls,
                    "max_tokens_per_call": self.max_tokens,
                    "max_reserved_output_tokens": self.max_total_tokens,
                    "reserved_output_tokens": self.reserved_output_tokens,
                    "reported_completion_tokens": self.completion_tokens,
                    "unknown_usage_calls": self.unknown_usage_calls,
                    "input_budget": f"{MAX_BODY} request bytes; input tokens unmeasured",
                    "rejections": list(self.rejections)}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        # Wait for pending forwards before any verifier starts.
        self.server.server_close()
        self.thread.join()
        self.path.unlink(missing_ok=True)
