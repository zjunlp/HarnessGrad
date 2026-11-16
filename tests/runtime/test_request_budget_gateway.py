"""Offline wire tests for the two production handlers (no model service)."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eval.service.native_model_route import NativeModelRoute
from eval.service.modelgate import ModelGateway
from eval.service.request_budget import TaskHTTPBudget, task_http_context
from harnessgrad.budget_integration import bind_budget


class Provider(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    calls = []
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.server.calls.append(body)
        data = b'data: {"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\ndata: [DONE]\n\n'
        if getattr(self.server, 'missing_usage', False):
            data = b'data: [DONE]\n\n'
        data = getattr(self.server, 'wire_body', data)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data) + getattr(self.server, 'extra_length', 0)))
        self.send_header('Connection', 'close')
        self.end_headers()
        if hasattr(self.server, "release_response"):
            self.server.release_response.wait(2)
        try:
            self.wfile.write(data)
        except OSError:
            pass
    def log_message(self, *args):
        pass


@pytest.fixture
def provider():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def binding(tmp_path, calls=1):
    return bind_budget({'limits': {'max_calls': calls},
                        'task_http': {'mode': 'strict', 'tokenizer_contract': 'fixture-v1'}},
                       tmp_path / 'ledger.train.json', run_id='wire::task:train', arm_id='agent')


def request(route, native):
    sock = socket.socket(socket.AF_UNIX) if native else socket.socket()
    sock.settimeout(5)
    sock.connect(str(route.socket_path) if native else ('127.0.0.1', route.port))
    body = b'{ "model": "fixture", "messages": [], "max_tokens": 4, "stream": true }'
    raw = b'POST /v1/chat/completions HTTP/1.1\r\nHost: original\r\nConnection: close\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
    sock.sendall(raw)
    result = bytearray()
    while True:
        data = sock.recv(65536)
        if not data:
            break
        result.extend(data)
    sock.close()
    return bytes(result), body


@pytest.mark.parametrize('native', [True, False])
def test_real_gateway_concurrent_admission_and_sse(tmp_path, provider, native):
    ledger = binding(tmp_path)
    ctx = TaskHTTPBudget(ledger, 'train', lambda **kw: 3, 'fixture-v1')
    target = provider.server_address
    route = (NativeModelRoute(f'http://{target[0]}:{target[1]}', budget_context=ctx) if native
             else ModelGateway(bind_host='127.0.0.1', target=target, budget_context=ctx)).start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: request(route, native), range(2)))
        assert len(provider.calls) == 1
        assert provider.calls[0] == results[0][1]
        assert sum(b'200 OK' in value for value, _ in results) == 1
        assert sum(b'429 Budget Stop' in value for value, _ in results) == 1
        success = next(value for value, _ in results if b'200 OK' in value)
        assert success.endswith(b'data: [DONE]\n\n')
        assert ledger.dispatcher.snapshot()['spent']['total_tokens'] == 5
    finally:
        route.stop()


@pytest.mark.parametrize('native', [True, False])
def test_no_tokenizer_never_connects(tmp_path, provider, native):
    ctx = TaskHTTPBudget(binding(tmp_path), 'train')
    target = provider.server_address
    route = (NativeModelRoute(f'http://{target[0]}:{target[1]}', budget_context=ctx) if native
             else ModelGateway(bind_host='127.0.0.1', target=target, budget_context=ctx)).start()
    try:
        raw, _ = request(route, native)
        assert b'exact_tokenization_unavailable' in raw
        assert provider.calls == []
        assert ctx.binding.dispatcher.snapshot()['counts']['calls'] == 0
    finally:
        route.stop()


def test_operator_tokenizer_command(tmp_path):
    script = tmp_path / 'tokenizer.py'
    script.write_text('import json,sys\nx=json.load(sys.stdin)\njson.dump(dict(exact=True, model=x["model"], contract_id=x["contract_id"], prompt_tokens=3),sys.stdout)\n')
    executable = str(Path(sys.executable).resolve())
    policy = {'limits': {'max_calls': 1}, 'task_http': {'mode': 'strict', 'tokenizer_contract': 'fixture',
              'tokenizer': {'argv': [executable, str(script)], 'models': ['fixture'],
                            'sha256': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                       for p in (executable, str(script))}}}}
    ledger = bind_budget(policy, tmp_path / 'ledger.train.json', run_id='command::task:train', arm_id='agent')
    ctx = task_http_context(ledger, 'train')
    ticket = ctx.admit('POST', '/v1/completions', {}, b'{"model":"fixture","max_tokens":4}')
    assert ticket.prompt_tokens == 3
    ledger.dispatcher.reconcile(ticket.request_id, None)
    with pytest.raises(Exception, match='unknown_usage'):
        ledger.dispatcher.reserve('task', 0, 0)


@pytest.mark.parametrize('native', [True, False])
def test_missing_final_usage_retains_reservation_and_stops(tmp_path, provider, native):
    provider.missing_usage = True
    ledger = binding(tmp_path, calls=5)
    ctx = TaskHTTPBudget(ledger, 'train', lambda **kw: 3, 'fixture-v1')
    target = provider.server_address
    route = (NativeModelRoute(f'http://{target[0]}:{target[1]}', budget_context=ctx) if native
             else ModelGateway(bind_host='127.0.0.1', target=target, budget_context=ctx)).start()
    try:
        first, _ = request(route, native)
        second, _ = request(route, native)
        assert b'200 OK' in first
        assert b'429 Budget Stop' in second
        assert len(provider.calls) == 1
        snapshot = ledger.dispatcher.snapshot()
        assert snapshot['reserved']['total_tokens'] == 7
        assert snapshot['unknown_requests']
        assert any(stop['reason'] == 'unknown_usage' for stop in route.budget_stops)
    finally:
        route.stop()


def test_read_only_at_cap(tmp_path):
    ledger = binding(tmp_path)
    ctx = TaskHTTPBudget(ledger, 'train', lambda **kw: 3, 'fixture-v1')
    assert ctx.admission_stop() is None
    ticket = ledger.dispatcher.reserve('task', 3, 4)
    ledger.dispatcher.reconcile(ticket.request_id, {'prompt_tokens': 3, 'completion_tokens': 2})
    before = ledger.dispatcher.snapshot()
    assert ctx.admission_stop() == {'reason': 'budget_exhausted', 'counter': 'calls', 'current': 1, 'limit': 1}
    assert ledger.dispatcher.snapshot() == before


@pytest.mark.parametrize('native', [True, False])
def test_account_waits_final_byte_settlement(tmp_path, provider, native, monkeypatch):
    ledger = binding(tmp_path)
    ctx = TaskHTTPBudget(ledger, 'train', lambda **kw: 3, 'fixture-v1')
    entered, release = threading.Event(), threading.Event()
    original = ledger.dispatcher.reconcile
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(4)
        return original(*args, **kwargs)
    monkeypatch.setattr(ledger.dispatcher, 'reconcile', delayed)
    target = provider.server_address
    route = (NativeModelRoute(f'http://{target[0]}:{target[1]}', budget_context=ctx) if native
             else ModelGateway(bind_host='127.0.0.1', target=target, budget_context=ctx)).start()
    try:
        sock = socket.socket(socket.AF_UNIX) if native else socket.socket()
        sock.settimeout(5)
        sock.connect(str(route.socket_path) if native else ('127.0.0.1', route.port))
        body = b'{"model":"fixture","max_tokens":4}'
        sock.sendall(b'POST /v1/completions HTTP/1.1\r\nHost: fixture\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        raw = b''
        while not raw.endswith(b'data: [DONE]\n\n'):
            raw += sock.recv(4096)
        assert entered.wait(1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            evidence = pool.submit(route.account)
            with pytest.raises(concurrent.futures.TimeoutError):
                evidence.result(timeout=.05)
            release.set()
            assert evidence.result(timeout=2)['budget_stops'] == []
        assert ledger.dispatcher.snapshot()['spent']['total_tokens'] == 5
        sock.close()
    finally:
        release.set()
        route.stop()


def test_usage_observation_bounded_and_numeric_raw_only(tmp_path):
    from eval.service.request_budget import UsageObserver
    observer = UsageObserver('text/event-stream')
    observer.feed(b'data: ' + b'x' * (observer.LIMIT + 1))
    assert observer.finish() is None
    assert len(observer.buffer) == 0
    ledger = binding(tmp_path)
    ticket = ledger.dispatcher.reserve('task', 3, 4)
    with pytest.raises(Exception):
        ledger.dispatcher.reconcile(ticket.request_id, {'prompt_tokens': 3, 'completion_tokens': 2,
              'total_tokens': 999, 'authorization': 'never-retain-this',
              'prompt_tokens_details': {'cached_tokens': 1, 'config': 'never-retain-this'}})
    raw = ledger.dispatcher.snapshot()['requests'][ticket.request_id]['raw_usage']
    assert raw['total_tokens'] == 999
    assert raw['prompt_tokens_details'] == {'cached_tokens': 1}
    assert 'never-retain-this' not in json.dumps(raw)


@pytest.mark.parametrize('native', [True, False])
def test_oversized_request_refused_before_provider(tmp_path, provider, native):
    ctx = TaskHTTPBudget(binding(tmp_path), 'train', lambda **kw: 3, 'fixture-v1')
    target = provider.server_address
    route = (NativeModelRoute(f'http://{target[0]}:{target[1]}', budget_context=ctx) if native
             else ModelGateway(bind_host='127.0.0.1', target=target, budget_context=ctx)).start()
    try:
        sock = socket.socket(socket.AF_UNIX) if native else socket.socket()
        sock.settimeout(5)
        sock.connect(str(route.socket_path) if native else ('127.0.0.1', route.port))
        sock.sendall(b'POST /v1/completions HTTP/1.1\r\nHost: fixture\r\nContent-Length: 16777217\r\n\r\n')
        raw = sock.recv(4096)
        assert b'request_body_too_large' in raw
        assert provider.calls == []
        assert ctx.binding.dispatcher.snapshot()['counts']['calls'] == 0
        sock.close()
    finally:
        route.stop()


@pytest.mark.parametrize('native', [True, False])
def test_operator_request_deadline_leaves_unknown(tmp_path, provider, native):
    provider.release_response = threading.Event()
    ledger = bind_budget({'limits': {'request_timeout_s': .1},
                          'task_http': {'mode': 'strict', 'tokenizer_contract': 'fixture-v1'}},
                         tmp_path / 'deadline.train.json', run_id='deadline::task:train', arm_id='agent')
    ctx = TaskHTTPBudget(ledger, 'train', lambda **kw: 3, 'fixture-v1')
    target = provider.server_address
    route = (NativeModelRoute(f'http://{target[0]}:{target[1]}', budget_context=ctx) if native
             else ModelGateway(bind_host='127.0.0.1', target=target, budget_context=ctx)).start()
    try:
        request(route, native)
        route.account()
        assert ledger.dispatcher.snapshot()['unknown_requests']
        assert route.budget_stops
    finally:
        provider.release_response.set()
        route.stop()


@pytest.mark.parametrize('native', [True, False])
@pytest.mark.parametrize('variant', ['no_terminal', 'done', 'completed', 'truncated', 'empty_usage', 'stale_usage'])
def test_sse_requires_authoritative_terminal(tmp_path, provider, native, variant):
    usage = b'data: {"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    payloads = {
        'no_terminal': usage,
        'stale_usage': usage + b'data: {"choices":[{"delta":{"content":"later"}}]}\n\ndata: [DONE]\n\n',
        'done': usage + b'data: [DONE]\n\n',
        'completed': b'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n',
        'truncated': usage + b'data: [DONE]\n\n',
        'empty_usage': b'data: {"usage":{}}\n\ndata: [DONE]\n\n',
    }
    provider.wire_body = payloads[variant]
    if variant == 'truncated':
        provider.extra_length = 10
    ledger = binding(tmp_path, calls=5)
    ctx = TaskHTTPBudget(ledger, 'train', lambda **kw: 3, 'fixture-v1')
    target = provider.server_address
    route = (NativeModelRoute(f'http://{target[0]}:{target[1]}', budget_context=ctx) if native
             else ModelGateway(bind_host='127.0.0.1', target=target, budget_context=ctx)).start()
    try:
        raw, _ = request(route, native)
        assert raw.split(b'\r\n\r\n', 1)[1] == payloads[variant]
        route.account()
        state = ledger.dispatcher.snapshot()
        if variant in ('done', 'completed'):
            assert not state['unknown_requests']
            assert state['spent']['total_tokens'] == 5
        else:
            assert state['unknown_requests']
            assert state['reserved']['total_tokens'] == 7
            assert ctx.admission_stop()['reason'] == 'unknown_usage'
            if variant == 'empty_usage':
                record = next(iter(state['requests'].values()))
                assert record['raw_usage'] == {}
    finally:
        route.stop()
