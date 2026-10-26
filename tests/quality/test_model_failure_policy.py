"""HTTP policy admission, passive framing controls and shared local fixtures."""
import http.server
import threading

import pytest

from data.scaffolds.registry import _check_verifier
from eval.run import model_failure
from eval.service.http_observation import HTTPObservation


@pytest.fixture
def endpoint():
    statuses, requests = [], []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers['Content-Length'])))
            status = statuses.pop(0)
            body = b'{"error":{"message":"fixture"}}' if status >= 400 else b'{}'
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', statuses, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def observed(status=503, stderr='openai.InternalServerError: Error code: 503', **extra):
    return {'exit_code': 1, 'stderr': stderr, 'verdict': {'passed': False, 'score': 0},
            'runtime_services': {'agent_model_http': {'version': 1, 'complete': True,
                'calls': [{'operation': 'chat.completions', 'status': status, 'complete': True}]}}, **extra}


@pytest.mark.parametrize('spec', [None, False, {}, {'version': 1},
    {'version': True, 'invalidate_on_http_status': [503]},
    {'version': 2, 'invalidate_on_http_status': [503]},
    {'version': 1, 'invalidate_on_http_status': [503], 'extra': 0}])
def test_invalid_declaration_rejected(spec):
    with pytest.raises(ValueError, match='model_failure_policy'):
        _check_verifier({'kind': 'answer', 'expected': '', 'model_failure_policy': spec}, 't', 'fixture')


@pytest.mark.parametrize('overrides', [
    {'sandbox': False}, {'env': {'kind': 'exec'}}, {'setup': {'workers': []}},
    {'env': {'kind': 'files'}},
])
def test_unsupported_routes_rejected(monkeypatch, overrides):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    args = dict(sandbox=True, setup=None, env={'kind': 'files', 'network': 'none'})
    args.update(overrides)
    with pytest.raises(ValueError, match='model_failure_policy'):
        model_failure.admit(**args)


def test_observer_frames_keepalive_chunked_interim_head_and_body_text():
    obs = HTTPObservation()
    pair = obs.connection()
    requests = (b'POST /v1/chat/completions HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}'
        b'HEAD /models HTTP/1.1\r\n\r\n'
        b'POST /v1/responses HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n')
    for byte in requests:
        pair.feed('client', bytes([byte]))
    body = b'HTTP/1.1 503 fake body\r\n\r\n'
    replies = (b'HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
        + hex(len(body))[2:].encode() + b';ext=ok\r\n' + body + b'\r\n0\r\nX-Trailer: ok\r\n\r\n'
        b'HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n'
        b'HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n')
    for byte in replies:
        pair.feed('upstream', bytes([byte]))
    report = obs.report()
    assert report['complete'] and not report['errors']
    assert [(c['operation'], c['status']) for c in report['calls']] == [
        ('chat.completions', 200), ('other', 200), ('responses', 503)]
    assert all(c['complete'] for c in report['calls'])


def test_observer_marks_incomplete_and_unknown_traffic():
    obs = HTTPObservation()
    pair = obs.connection()
    pair.feed('client', b'POST /v1/completions HTTP/1.1\r\nContent-Length: 0\r\n\r\n')
    pair.feed('upstream', b'HTTP/1.1 503 Unavailable\r\nContent-Length: 20\r\n\r\nshort')
    assert not obs.report()['complete']
    pair.feed('upstream', b' enough body!!!')
    pair.feed('upstream', b'invalid headers\r\n\r\n')
    assert not obs.report()['complete'] and obs.report()['errors']
