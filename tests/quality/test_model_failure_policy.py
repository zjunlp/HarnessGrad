"""Observe actual model HTTP failures; default execution and recovered calls stay unchanged."""
import copy
import http.server
import json
import threading

import pytest

from data.scaffolds.registry import _check_verifier
from eval.run import model_failure, runner
from eval.service.http_observation import HTTPObservation
from test_declared_trials_continue import PROTOCOL
from test_shared_dataset_runtime import harness


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


@pytest.mark.parametrize('spec', [None, False, {}, {'version': True}, {'version': 2}, {'version': 1, 'extra': 0}])
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


@pytest.mark.parametrize('result', [
    observed(400, 'openai.BadRequestError: context length exceeded'),
    observed(401, 'openai.AuthenticationError: invalid key'),
    observed(stderr='KeyError: unrelated'),
    observed(stderr='InternalServerError: earlier\nKeyError: terminal'),
    observed(exit_code=0), observed(verdict={'passed': True, 'score': 1}),
    observed(runtime_services={}), observed(invalid={'stage': 'other failure'}),
])
def test_counterexamples_keep_original_classification(result):
    original = copy.deepcopy(result)
    assert model_failure.failure(result) is None
    assert result == original


@pytest.mark.parametrize('status,tail', [(429, 'openai.RateLimitError: unavailable'),
    (500, 'openai.InternalServerError: unavailable'), (503, 'urllib.error.HTTPError: HTTP Error 503')])
def test_matching_terminal_failure_requires_platform_receipt(status, tail):
    result = observed(status, tail)
    assert model_failure.failure(result)['http_status'] == status
    result['runtime_services']['agent_model_http']['complete'] = False
    assert model_failure.failure(result) is None


@pytest.mark.parametrize('status,tail', [(429, 'openai.RateLimitError: unavailable'),
    (503, 'openai.InternalServerError: unavailable'), (503, 'urllib.error.HTTPError: HTTP Error 503')])
def test_other_successful_requests_do_not_prove_recovery(status, tail):
    result = observed(status, tail)
    result['runtime_services']['agent_model_http']['calls'].append(
        {'operation': 'chat.completions', 'status': 200, 'complete': True})
    assert model_failure.failure(result)['http_status'] == status
    result['stderr'] = 'KeyError: unrelated'
    assert model_failure.failure(result) is None
    result.update(stderr=tail, exit_code=0)
    assert model_failure.failure(result) is None


@pytest.mark.parametrize('mode,expected', [('503', 'unmeasured'), ('429', 'unmeasured'),
    ('400', 'measured'), ('recover', 'measured'), ('unrelated', 'measured'), ('pass', 'measured'),
    ('independent', 'unmeasured')])
@pytest.mark.parametrize('enabled', [False, True])
def test_real_model_route_reaches_trial_classification(tmp_path, monkeypatch, endpoint, mode, expected, enabled):
    url, statuses, requests = endpoint
    statuses[:] = ([503, 200] if mode in ('recover', 'independent') else [int(mode) if mode.isdigit() else 503]) * 3
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HG_AGENT_BASE_URL', url)
    repo = harness(work, f'''import urllib.request, urllib.error
def request():
    return urllib.request.urlopen(urllib.request.Request(os.environ['HG_AGENT_BASE_URL']+'/chat/completions',
        data=b'{{}}', headers={{'Content-Type':'application/json'}})).read()
mode={mode!r}
if mode == 'pass': (w/'answer.txt').write_text('yes')
if mode in ('recover', 'unrelated'):
    try: request()
    except urllib.error.HTTPError: pass
    if mode == 'unrelated': raise KeyError('unrelated')
    request()
elif mode == 'independent':
    from concurrent.futures import ThreadPoolExecutor, wait
    with ThreadPoolExecutor() as pool:
        failed = pool.submit(request)
        wait([failed])  # Defer observing the failed future until another request succeeds.
        pool.submit(request).result()
        failed.result()
else: request()
''')
    spec = {'kind': 'answer', 'expected': 'yes', 'trial_protocol': PROTOCOL}
    if enabled:
        spec['model_failure_policy'] = {'version': 1}
    result = runner.evaluate(repo, [{'task_id': 'fixture/t', 'goal': ''}], {}, trials=3, jobs=1,
        envs={'fixture/t': {'kind': 'files', 'network': 'none'}}, verifiers={'fixture/t': spec},
        run_id='fixture', harness_sha='fixture', progress_dir=tmp_path/'run')
    rows = result['repeated_trials']['results']
    want = expected if enabled else 'measured'
    assert [r['status'] for r in rows] == [want] * 3
    assert [r['score'] for r in rows] == [None if want == 'unmeasured' else int(mode == 'pass')] * 3
    assert result['repeated_trials']['complete'] == (want == 'measured')
    assert all(('agent_model_http' in (r['runtime_services'] or {})) == enabled for r in rows)
    assert len(requests) == (6 if mode in ('recover', 'independent') else 3) and not statuses
    assert not (tmp_path/'run/trial-evidence').exists()  # Classification is independent of archival.


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


def test_observer_incomplete_and_unknown_traffic_is_not_failure_proof():
    obs = HTTPObservation()
    pair = obs.connection()
    pair.feed('client', b'POST /v1/completions HTTP/1.1\r\nContent-Length: 0\r\n\r\n')
    pair.feed('upstream', b'HTTP/1.1 503 Unavailable\r\nContent-Length: 20\r\n\r\nshort')
    assert not obs.report()['complete']
    pair.feed('upstream', b' enough body!!!')
    pair.feed('upstream', b'invalid headers\r\n\r\n')
    assert not obs.report()['complete'] and obs.report()['errors']
