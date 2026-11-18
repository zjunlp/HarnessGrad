"""Local scripted Responses and stdio controls for the personal Codex probe.

These are diagnostic fixtures, not a model client or production host adapter.
No I/O occurs on import. Each server and process is owned by one probe.
"""
from __future__ import annotations

import json
from collections import deque
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from harnessgrad.improvement.contracts import reject_json_constant, unique_json_object

MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024
MODEL = 'harnessgrad-phase0-fixture'


def decode(data: str | bytes) -> dict:
    value = json.loads(data, object_pairs_hook=unique_json_object,
                       parse_constant=reject_json_constant)
    if not isinstance(value, dict):
        raise ValueError('probe protocol message must be an object')
    return value


def message_text(payload: dict) -> str:
    """Only model-visible message text, excluding tool schemas and arguments."""
    return '\n'.join(part['text'] for item in payload.get('input', [])
                     if item.get('type') == 'message'
                     for part in item.get('content', [])
                     if part.get('type') == 'input_text' and isinstance(part.get('text'), str))


def tool_outputs(payload: dict) -> str:
    return '\n'.join(item['output'] for item in payload.get('input', [])
                     if item.get('type') == 'function_call_output'
                     and isinstance(item.get('output'), str))


def response_events(number: int, item: dict) -> bytes:
    response_id = f'resp_phase0_{number}'
    events = [
        {'type': 'response.created', 'response': {'id': response_id}},
        {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
    ]
    if item['type'] == 'message':
        events.append({'type': 'response.output_text.delta', 'output_index': 0,
                       'content_index': 0, 'delta': item['content'][0]['text']})
    events.extend([
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
        {'type': 'response.completed', 'response': {
            'id': response_id, 'status': 'completed', 'output': [item],
            # Explicitly synthetic usage; never interpreted as real-model cost.
            'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}},
    ])
    return ''.join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                   for event in events).encode('utf-8')


class ScriptedResponses:
    """At most two local requests per planned turn; unexpected routes fail."""

    def __init__(self, calls: list[dict]):
        self.calls = calls
        self.requests: list[dict] = []
        self.errors: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.connection.settimeout(5)
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if self.path != '/v1/responses' or not 0 < size <= MAX_MESSAGE_BYTES:
                        raise ValueError('unexpected fixture route or body size')
                    payload = decode(self.rfile.read(size))
                    if payload.get('model') != MODEL:
                        raise ValueError('unexpected fixture model')
                    auth = self.headers.get('Authorization')
                    if auth and auth != 'Bearer phase0-fixture-key':
                        raise ValueError('unexpected credential at fixture endpoint')
                    number = len(owner.requests)
                    if number >= 2 * len(owner.calls):
                        raise ValueError('scripted request budget exhausted')
                    owner.requests.append({'path': self.path, 'payload': payload})
                    call = owner.calls[number // 2]
                    if number % 2 == 0:
                        item = {'type': 'function_call', 'id': f'fc_{number}',
                                'call_id': f'call_{number}', 'name': call['name'],
                                'arguments': json.dumps(call['arguments'])}
                    else:
                        item = {'type': 'message', 'id': f'msg_{number}', 'role': 'assistant',
                                'status': 'completed', 'content': [
                                    {'type': 'output_text', 'text': 'Synthetic turn completed.'}]}
                    body = response_events(number, item)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (ValueError, OSError, TypeError, RecursionError):
                    owner.errors.append('invalid or failed scripted request')
                    self.send_error(400, 'invalid phase0 fixture request')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.server.server_port}/v1'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class AppServer:
    """Small bounded JSONL client; never connects to an existing daemon."""

    def __init__(self, argv: list[str], *, cwd: Path, env: dict[str, str], deadline: float):
        self.deadline = deadline
        self.events: list[dict] = []
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.stdout_truncated = False
        self.stderr_truncated = False
        self.messages: queue.Queue = queue.Queue()
        self.sequence = 0
        self.failure: str | None = None
        self.deferred: deque[dict] = deque()
        self.proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.readers = [threading.Thread(target=self._read, daemon=True),
                        threading.Thread(target=self._read_errors, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _read(self):
        total = 0
        try:
            while line := self.proc.stdout.readline(MAX_MESSAGE_BYTES + 1):
                total += len(line)
                remaining = max(0, MAX_TRANSCRIPT_BYTES - len(self.stdout))
                self.stdout.extend(line[:remaining])
                if len(line) > MAX_MESSAGE_BYTES or total > MAX_TRANSCRIPT_BYTES:
                    self.stdout_truncated = True
                    raise ValueError('app-server transcript exceeds probe limit')
                event = decode(line)
                self.events.append(event)
                self.messages.put(event)
        except (ValueError, OSError, RecursionError):
            self.failure = 'invalid or oversized app-server transcript'
        finally:
            self.messages.put(None)

    def _read_errors(self):
        while data := self.proc.stderr.read(4096):
            if len(self.stderr) + len(data) <= MAX_TRANSCRIPT_BYTES:
                self.stderr.extend(data)
            else:
                remaining = max(0, MAX_TRANSCRIPT_BYTES - len(self.stderr))
                self.stderr.extend(data[:remaining])
                self.stderr_truncated = True
                self.failure = 'app-server stderr exceeds probe limit'
                self.messages.put(None)
                return

    def send(self, value: dict) -> None:
        body = (json.dumps(value, allow_nan=False) + '\n').encode('utf-8')
        if len(body) > MAX_MESSAGE_BYTES:
            raise ValueError('probe request exceeds limit')
        self.proc.stdin.write(body)
        self.proc.stdin.flush()

    def receive(self, handler: Callable[[dict], dict] | None = None) -> dict:
        if self.failure:
            raise RuntimeError(self.failure)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('personal Codex probe deadline exceeded')
        if handler is not None and self.deferred:
            value = self.deferred.popleft()
        else:
            try:
                value = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError('personal Codex probe deadline exceeded') from None
        if value is None:
            raise RuntimeError(self.failure or 'app-server exited before probe completed')
        if 'method' in value and 'id' in value:
            if value['method'] == 'item/tool/call':
                if handler is None:
                    self.deferred.append(value)
                else:
                    self.send({'id': value['id'], 'result': handler(value['params'])})
            else:
                self.send({'id': value['id'], 'error': {
                    'code': -32601, 'message': 'request is outside the phase0 fixture'}})
                raise RuntimeError('unexpected app-server request')
        return value

    def request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        self.send({'id': request_id, 'method': method, 'params': params})
        while True:
            value = self.receive()
            if value.get('id') == request_id and 'method' not in value:
                if 'error' in value or not isinstance(value.get('result'), dict):
                    raise RuntimeError(f'app-server refused {method}')
                return value['result']

    def initialize(self) -> dict:
        result = self.request('initialize', {'clientInfo': {
            'name': 'harnessgrad_phase0', 'version': '0.1.0'},
            'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized', 'params': {}})
        return result

    def wait_turn(self, thread_id: str, turn_id: str, handler: Callable[[dict], dict]) -> dict:
        while True:
            value = self.receive(handler)
            if value.get('method') == 'turn/completed':
                params = value['params']
                if params.get('threadId') != thread_id or params['turn']['id'] != turn_id:
                    raise RuntimeError('completion belongs to a different probe turn')
                return params['turn']

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for reader in self.readers:
            reader.join(timeout=2)
