"""Synthetic loopback endpoint; no external model, network, SDK or Docker."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'methods'))
import editor


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(editor.endpoint_client.time, 'sleep', lambda _: None)


@pytest.fixture
def endpoint(monkeypatch):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    requests = []
    plan = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            requests.append((self.path, self.headers.get('Authorization'), json.loads(body)))
            status, payload, headers = plan.pop(0)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(payload)
            except BrokenPipeError:
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests, plan
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        assert not thread.is_alive()


def response(content='{"no_change":"synthetic"}', usage=None):
    return json.dumps({'choices': [{'message': {'content': content}}], 'model': 'fixture-model',
                       'usage': usage}).encode()


def setup_editor(monkeypatch, url):
    monkeypatch.setenv('HG_METHOD_BASE_URL', url)
    monkeypatch.setenv('HG_METHOD_API_KEY', 'synthetic-secret-key')
    monkeypatch.setenv('HG_METHOD_MODEL', 'fixture-model')
    monkeypatch.setenv('HG_METHOD_PARSE_RETRIES', '1')
    monkeypatch.setenv('HG_METHOD_TIMEOUT_S', '2')


def test_retries_usage_and_success_trace(endpoint, monkeypatch, tmp_path):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    monkeypatch.setenv('HG_CALL_LOG_DIR', str(tmp_path))
    plan.extend([(429, b'secret failure body', {}), (503, b'secret failure body', {}),
                 (200, response(usage={'prompt_tokens': 12, 'completion_tokens': 3,
                                       'prompt_tokens_details': {'cached_tokens': 5}}), {})])
    editor.reset_spent()
    assert editor.ask('synthetic prompt') == {'no_change': 'synthetic'}
    assert len(requests) == 3
    path, auth, payload = requests[-1]
    assert path == '/v1/chat/completions' and auth == 'Bearer synthetic-secret-key'
    assert payload['model'] == 'fixture-model' and payload['temperature'] == 0.0
    assert [entry['role'] for entry in payload['messages']] == ['system', 'user']
    assert editor.spent()['calls'] == 1 and editor.spent()['input'] == 7
    assert editor.spent()['output'] == 3 and editor._SPENT['cache_read'] == 5
    assert editor.cache_read_tokens() is None and editor.spent_tokens() is None
    trace, = (tmp_path / 'endpoint').glob('*.json')
    assert json.loads(trace.read_text())['transport_attempts'] == 3
    assert 'synthetic-secret-key' not in trace.read_text()


def test_redirect_is_refused_and_never_forwards_key(endpoint, monkeypatch):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    plan.append((302, b'synthetic-secret-key private body', {'Location': url + '/stolen'}))
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        editor.ask('fixture')
    assert 'HTTP 302' in str(exc.value) and 'synthetic-secret-key' not in str(exc.value)
    assert len(requests) == 1


@pytest.mark.parametrize('payload', [b'not json', b'{}', b'{"choices":[{"message":{"content":123}}]}'])
def test_bad_response_schema_is_error_not_no_change(endpoint, monkeypatch, payload):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    plan.append((200, payload, {}))
    with pytest.raises(editor.endpoint_client.EndpointError, match='invalid method endpoint response'):
        editor.ask('fixture')
    assert len(requests) == 1


def test_bounded_status_retries_and_no_error_body_leak(endpoint, monkeypatch):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    plan.extend([(503, b'synthetic-secret-key and private body', {})] * 3)
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        editor.ask('fixture')
    assert str(exc.value) == 'method endpoint HTTP 503 after 3 attempt(s)'
    assert len(requests) == 3


def test_response_size_limit_is_an_error(endpoint, monkeypatch):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    monkeypatch.setattr(editor.endpoint_client, 'MAX_RESPONSE_BYTES', 128)
    plan.append((200, b'x' * 129, {}))
    with pytest.raises(editor.endpoint_client.EndpointError, match='exceeds limit'):
        editor.ask('fixture')
    assert len(requests) == 1


def test_cold_venv_without_sdk_can_call_local_endpoint(endpoint, tmp_path):
    url, requests, plan = endpoint
    plan.append((200, response(), {}))
    platform = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(platform)
    code = '''import importlib.util,sys
assert importlib.util.find_spec("openai") is None
sys.path.insert(0, sys.argv[1]); import editor
assert editor.ask("cold fixture") == {"no_change":"synthetic"}
'''
    env = {'HOME': str(tmp_path), 'PATH': os.defpath, 'NO_PROXY': '127.0.0.1,localhost',
           'HG_METHOD_BASE_URL': url, 'HG_METHOD_API_KEY': 'synthetic-secret-key',
           'HG_METHOD_MODEL': 'fixture-model', 'HG_METHOD_PARSE_RETRIES': '1'}
    result = subprocess.run([str(platform / 'bin/python'), '-I', '-B', '-c', code, str(ROOT / 'methods')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(requests) == 1


@pytest.mark.parametrize('status', [408, 409, 429, 500, 503])
def test_retryable_http_statuses_retry_at_most_twice(endpoint, monkeypatch, status):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    plan.extend([(status, b'discarded', {}), (200, response(), {})])
    assert editor.ask('fixture') == {'no_change': 'synthetic'}
    assert len(requests) == 2


def test_nonretryable_http_status_does_not_retry(endpoint, monkeypatch):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    plan.append((401, b'sensitive server body', {}))
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        editor.ask('fixture')
    assert str(exc.value) == 'method endpoint HTTP 401 after 1 attempt(s)'
    assert len(requests) == 1


def test_transport_failure_is_bounded_and_does_not_reveal_exception(monkeypatch):
    client = editor.endpoint_client.Client(base_url='http://127.0.0.1:1/v1',
                                           api_key='synthetic-secret', timeout=1)
    calls = []
    class Opener:
        def open(self, *args, **kwargs):
            calls.append(True)
            raise TimeoutError('synthetic-secret https://user:pass@private.invalid')
    client.opener = Opener()
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        client.complete(model='synthetic', temperature=0, messages=[])
    assert str(exc.value) == 'method endpoint transport failed after 3 attempt(s)'
    assert len(calls) == 3


@pytest.mark.parametrize('url', ['http://user:password@127.0.0.1/v1',
                                  'https://example.invalid/v1?token=secret',
                                  'http://127.0.0.1/v1#fragment'])
def test_sensitive_or_ambiguous_base_url_is_rejected(url):
    with pytest.raises(editor.endpoint_client.EndpointError, match='invalid method endpoint URL'):
        editor.endpoint_client.Client(base_url=url, api_key='fixture')


def test_truncated_http_response_does_not_expose_partial_body():
    client = editor.endpoint_client.Client(base_url='http://127.0.0.1:1/v1',
                                           api_key='synthetic-secret', timeout=1)
    class Opener:
        def open(self, *args, **kwargs):
            raise http.client.IncompleteRead(b'synthetic-private-response')
    client.opener = Opener()
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        client.complete(model='synthetic', temperature=0, messages=[])
    assert str(exc.value) == 'method endpoint transport failed after 3 attempt(s)'
    assert 'synthetic-private-response' not in str(exc.value)


@pytest.mark.parametrize('usage', [
    {}, {'prompt_tokens': 1}, {'prompt_tokens': '1', 'completion_tokens': 2},
    {'prompt_tokens': True, 'completion_tokens': 2},
    {'prompt_tokens': 1, 'completion_tokens': -1},
    {'prompt_tokens': 1, 'completion_tokens': 2, 'prompt_tokens_details': []},
    {'prompt_tokens': 1, 'completion_tokens': 2, 'prompt_tokens_details': {'cached_tokens': 2}},
    {'prompt_tokens': 5, 'completion_tokens': 2, 'prompt_cache_hit_tokens': 3, 'cached_tokens': 4},
])
def test_invalid_usage_cannot_be_recorded_as_normal(endpoint, monkeypatch, usage):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    editor.reset_spent()
    plan.append((200, response(usage=usage), {}))
    with pytest.raises(editor.endpoint_client.EndpointError, match='invalid method endpoint response'):
        editor.ask('synthetic')
    assert editor.spent()['calls'] == 1
    assert editor.spent_tokens() is None and editor.spend_source() == 'endpoint_unreported'
    assert len(requests) == 1


def test_invalid_timeout_does_not_echo_credential(monkeypatch):
    secret = 'synthetic-credential-as-timeout'
    setup_editor(monkeypatch, 'http://127.0.0.1:1/v1')
    monkeypatch.setenv('HG_METHOD_API_KEY', secret)
    monkeypatch.setenv('HG_METHOD_TIMEOUT_S', secret)
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        editor.ask('fixture')
    assert str(exc.value) == 'invalid method endpoint timeout'
    assert secret not in str(exc.value)


def test_header_construction_exception_is_sanitized(monkeypatch):
    secret = 'synthetic-request-secret'
    client = editor.endpoint_client.Client(base_url='http://127.0.0.1:1/v1', api_key=secret)
    def broken(*args, **kwargs):
        raise ValueError(secret)
    monkeypatch.setattr(editor.endpoint_client, 'Request', broken)
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        client.complete(model='fixture', temperature=0, messages=[])
    assert secret not in str(exc.value)
    assert str(exc.value) == 'invalid method endpoint request after 1 attempt(s)'


def test_null_or_missing_content_preserves_usage_trace_and_parse_retry(endpoint, monkeypatch, tmp_path):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    monkeypatch.setenv('HG_METHOD_PARSE_RETRIES', '2')
    monkeypatch.setenv('HG_CALL_LOG_DIR', str(tmp_path))
    usage = {'prompt_tokens': 4, 'completion_tokens': 2,
             'prompt_tokens_details': {'cached_tokens': None}}
    first = json.dumps({'choices': [{'message': {'content': None}}], 'usage': usage}).encode()
    second = json.dumps({'choices': [{'message': {}}], 'usage': usage}).encode()
    plan.extend([(200, first, {}), (200, second, {})])
    editor.reset_spent()
    with pytest.raises(editor.UnparseableReply):
        editor.ask('fixture')
    assert len(requests) == 2 and editor.spent()['calls'] == 2
    assert editor.spent()['input'] == 8 and editor.spent()['output'] == 4
    records = [json.loads(p.read_text()) for p in (tmp_path / 'endpoint').glob('*.json')]
    assert len(records) == 2 and all(record['reply'] == '' for record in records)


def test_retry_after_and_explicit_no_retry(endpoint, monkeypatch):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    waits = []
    monkeypatch.setattr(editor.endpoint_client.time, 'sleep', waits.append)
    plan.extend([(429, b'private', {'Retry-After': '1.5'}), (200, response(), {})])
    assert editor.ask('fixture') == {'no_change': 'synthetic'}
    assert waits == [1.5] and len(requests) == 2
    plan.append((503, b'private', {'x-should-retry': 'false'}))
    with pytest.raises(editor.endpoint_client.EndpointError, match='HTTP 503 after 1 attempt'):
        editor.ask('fixture')
    assert len(requests) == 3 and waits == [1.5]
    plan.append((429, b'private', {'Retry-After': '60'}))
    with pytest.raises(editor.endpoint_client.EndpointError, match='retry delay exceeds limit'):
        editor.ask('fixture')
    assert len(requests) == 4 and waits == [1.5]


def test_platform_method_entrypoint_mounts_transport_and_makes_one_synthetic_call(endpoint, tmp_path, monkeypatch):
    from harnessgrad import methods
    from eval.containers import sandbox
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    plan.append((200, response(usage={'prompt_tokens': 8, 'completion_tokens': 2}), {}))
    scratch = tmp_path / 'scratch'
    base = scratch / 'base'
    work = scratch / 'work'
    base.mkdir(parents=True); work.mkdir()
    (base / 'harness.json').write_text('{"name":"synthetic","version":"1","entrypoint":"agent.py"}')
    (base / 'agent.py').write_text('# synthetic source\n')
    (base / '_harnessgrad').mkdir()
    harness_root = tmp_path / "harness-work"
    harness_root.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(harness_root))
    monkeypatch.setenv('HG_CALL_LOG_DIR', str(scratch / 'logs'))
    argv = [sys.executable, str(ROOT / 'methods/llm_improver/run.py')]
    request = {'platform_api_version': '0.1.0', 'base_harness': str(base),
               'workspace': str(work), 'trajectory_out': str(scratch / 'trajectory.json'),
               'task_ids': ['synthetic'], 'round_index': 1, 'incumbent_score': 0.0}
    plan_info = methods._method_sandbox_plan(work, argv, scratch, readonly=(base,))
    assert ROOT / 'methods' in plan_info['visible_paths']
    assert (ROOT / 'methods/endpoint_client.py').is_file()
    result = methods._call_entrypoint(work, argv, request, timeout_s=30, sandboxed=True,
                                      scratch=scratch, cwd=work, readonly=(base,))
    assert '_error' not in result, result
    assert result['changed'] is False and result['method_usage']['calls'] == 1
    assert len(requests) == 1
    assert (scratch / 'trajectory.json').is_file()
    trace, = (scratch / 'logs/endpoint').glob('*.json')
    assert json.loads(trace.read_text())['usage']['input'] == 8


def test_large_finite_timeout_remains_supported():
    client = editor.endpoint_client.Client(base_url='http://127.0.0.1:1/v1',
                                           api_key='synthetic', timeout=1801)
    assert client.timeout == 1801


def test_http_failure_after_requests_preserves_unknown_budget(endpoint, monkeypatch, tmp_path):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    editor.reset_spent()
    plan.extend([(503, b'synthetic-secret-key private body', {})] * 3)
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        editor.ask('synthetic')
    assert exc.value.attempts == 3 and 'private body' not in str(exc.value)
    assert len(requests) == 3 and editor.spent()['calls'] == 0
    assert editor.spent_tokens() is None and editor.spend_source() == 'endpoint_unreported'
    assert editor._SPENT['unknown_transport_attempts'] == 3
    emitted = []
    monkeypatch.setattr(editor, 'emit', emitted.append)
    editor.report({'workspace': str(tmp_path), 'trajectory_out': str(tmp_path / 'trajectory.json')},
                  method='fixture', harness_dir=tmp_path, changed=False, hypothesis='failure')
    record = json.loads((tmp_path / 'trajectory.json').read_text())['steps'][0]['claimed_cost']
    assert record['generation_tokens'] is None
    assert record['method_spend_source'] == 'endpoint_unreported'
    assert record['method_unknown_transport_attempts'] == 3


def test_ask_timeout_after_attempt_has_unknown_cost(monkeypatch):
    setup_editor(monkeypatch, 'http://127.0.0.1:1/v1')
    editor.reset_spent()
    original = editor.endpoint_client.Client
    class Opener:
        def open(self, *args, **kwargs):
            raise TimeoutError('synthetic-secret-key private error')
    def client(**kwargs):
        value = original(**kwargs)
        value.opener = Opener()
        return value
    monkeypatch.setattr(editor.endpoint_client, 'Client', client)
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        editor.ask('synthetic')
    assert exc.value.attempts == 3 and 'synthetic-secret-key' not in str(exc.value)
    assert editor.spent_tokens() is None and editor.spend_source() == 'endpoint_unreported'
    assert editor._SPENT['unknown_transport_attempts'] == 3


@pytest.mark.parametrize('key,value', [('HG_METHOD_BASE_URL', 'http://user:pass@localhost/v1'),
                                       ('HG_METHOD_TIMEOUT_S', 'synthetic-invalid-timeout-value'),
                                       ('HG_METHOD_API_KEY', 'invalid\nsecret')])
def test_configuration_failure_before_request_has_no_unknown_cost(monkeypatch, key, value):
    setup_editor(monkeypatch, 'http://127.0.0.1:1/v1')
    monkeypatch.setenv(key, value)
    editor.reset_spent()
    with pytest.raises(editor.endpoint_client.EndpointError) as exc:
        editor.ask('synthetic')
    assert exc.value.attempts == 0 and value not in str(exc.value)
    assert editor.spent_tokens() == 0 and editor.spend_source() is None
    assert editor._SPENT['unknown_transport_attempts'] == 0


def test_retried_failure_before_success_marks_partial_usage_unknown(endpoint, monkeypatch):
    url, requests, plan = endpoint
    setup_editor(monkeypatch, url)
    editor.reset_spent()
    plan.extend([(500, b'private', {}), (200, response(usage={
        'prompt_tokens': 5, 'completion_tokens': 2}), {})])
    assert editor.ask('synthetic') == {'no_change': 'synthetic'}
    assert len(requests) == 2 and editor.spent()['calls'] == 1
    assert editor.spent_tokens() is None and editor.spend_source() == 'endpoint_unreported'
    assert editor._SPENT['unknown_transport_attempts'] == 1
