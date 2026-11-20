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

from harnessgrad.improvement.model import JsonModel
from methods import endpoint_client

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(endpoint_client.time, 'sleep', lambda _: None)


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


def model(url, **options):
    settings = {
        'base_url': url, 'api_key': 'synthetic-secret-key', 'model': 'fixture-model',
        'max_calls': 4, 'system_prompt': 'Return a JSON object.', 'timeout': 2,
    }
    return JsonModel(**{**settings, **options})


def setup_editor(monkeypatch, url):
    monkeypatch.setenv('HG_METHOD_BASE_URL', url)
    monkeypatch.setenv('HG_METHOD_API_KEY', 'synthetic-secret-key')
    monkeypatch.setenv('HG_METHOD_MODEL', 'fixture-model')
    monkeypatch.setenv('HG_METHOD_PARSE_RETRIES', '1')
    monkeypatch.setenv('HG_METHOD_TIMEOUT_S', '2')


def test_retries_preserve_response_usage_and_unknown_attempts(endpoint):
    url, requests, plan = endpoint
    plan.extend([(429, b'secret failure body', {}), (503, b'secret failure body', {}),
                 (200, response(usage={'prompt_tokens': 12, 'completion_tokens': 3,
                                       'prompt_tokens_details': {'cached_tokens': 5}}), {})])
    client = endpoint_client.Client(base_url=url, api_key='synthetic-secret-key')
    messages = [{'role': 'system', 'content': 'Return a JSON object.'},
                {'role': 'user', 'content': 'synthetic prompt'}]
    result = client.complete(model='fixture-model', temperature=0.0, messages=messages)
    assert result.transport_attempts == 3 and result.unknown_transport_attempts == 2
    assert result.usage.prompt_tokens == 12 and result.usage.completion_tokens == 3
    assert result.usage.prompt_tokens_details.cached_tokens == 5
    assert result.model == 'fixture-model'
    assert json.loads(result.choices[0].message.content) == {'no_change': 'synthetic'}
    assert len(requests) == 3
    path, auth, payload = requests[-1]
    assert path == '/v1/chat/completions' and auth == 'Bearer synthetic-secret-key'
    assert payload == {'model': 'fixture-model', 'temperature': 0.0, 'messages': messages}


def test_redirect_is_refused_and_never_forwards_key(endpoint):
    url, requests, plan = endpoint
    plan.append((302, b'synthetic-secret-key private body', {'Location': url + '/stolen'}))
    with pytest.raises(endpoint_client.EndpointError) as exc:
        model(url)('fixture')
    assert 'HTTP 302' in str(exc.value) and 'synthetic-secret-key' not in str(exc.value)
    assert len(requests) == 1


@pytest.mark.parametrize('payload', [b'not json', b'{}', b'{"choices":[{"message":{"content":123}}]}'])
def test_bad_response_schema_is_error_not_no_change(endpoint, payload):
    url, requests, plan = endpoint
    plan.append((200, payload, {}))
    with pytest.raises(endpoint_client.EndpointError, match='invalid method endpoint response'):
        model(url)('fixture')
    assert len(requests) == 1


def test_bounded_status_retries_and_no_error_body_leak(endpoint):
    url, requests, plan = endpoint
    plan.extend([(503, b'synthetic-secret-key and private body', {})] * 3)
    with pytest.raises(endpoint_client.EndpointError) as exc:
        model(url)('fixture')
    assert str(exc.value) == 'method endpoint HTTP 503 after 3 attempt(s)'
    assert len(requests) == 3


def test_response_size_limit_is_an_error(endpoint, monkeypatch):
    url, requests, plan = endpoint
    monkeypatch.setattr(endpoint_client, 'MAX_RESPONSE_BYTES', 128)
    plan.append((200, b'x' * 129, {}))
    with pytest.raises(endpoint_client.EndpointError, match='exceeds limit'):
        model(url)('fixture')
    assert len(requests) == 1


def test_cold_venv_without_sdk_can_call_local_endpoint(endpoint, tmp_path):
    url, requests, plan = endpoint
    plan.append((200, response(), {}))
    platform = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(platform)
    code = '''import importlib.util,sys
assert importlib.util.find_spec("openai") is None
sys.path.insert(0, sys.argv[1])
from harnessgrad.improvement.model import JsonModel
callback = JsonModel(base_url=sys.argv[2], api_key="synthetic-key", model="fixture-model",
                     max_calls=1, system_prompt="Return JSON.")
assert callback("cold fixture") == {"no_change":"synthetic"}
'''
    env = {'HOME': str(tmp_path), 'PATH': os.defpath, 'NO_PROXY': '127.0.0.1,localhost'}
    result = subprocess.run([str(platform / 'bin/python'), '-I', '-B', '-c', code, str(ROOT), url],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(requests) == 1


@pytest.mark.parametrize('status', [408, 409, 429, 500, 503])
def test_retryable_http_statuses_retry_at_most_twice(endpoint, status):
    url, requests, plan = endpoint
    plan.extend([(status, b'discarded', {}), (200, response(), {})])
    assert model(url)('fixture') == {'no_change': 'synthetic'}
    assert len(requests) == 2


def test_nonretryable_http_status_does_not_retry(endpoint):
    url, requests, plan = endpoint
    plan.append((401, b'sensitive server body', {}))
    with pytest.raises(endpoint_client.EndpointError) as exc:
        model(url)('fixture')
    assert str(exc.value) == 'method endpoint HTTP 401 after 1 attempt(s)'
    assert len(requests) == 1


def test_transport_failure_is_bounded_and_does_not_reveal_exception(monkeypatch):
    client = endpoint_client.Client(base_url='http://127.0.0.1:1/v1',
                                   api_key='synthetic-secret', timeout=1)
    calls = []
    class Opener:
        def open(self, *args, **kwargs):
            calls.append(True)
            raise TimeoutError('synthetic-secret https://user:pass@private.invalid')
    client.opener = Opener()
    with pytest.raises(endpoint_client.EndpointError) as exc:
        client.complete(model='synthetic', temperature=0, messages=[])
    assert str(exc.value) == 'method endpoint transport failed after 3 attempt(s)'
    assert len(calls) == 3


@pytest.mark.parametrize('url', ['http://user:password@127.0.0.1/v1',
                                  'https://example.invalid/v1?token=secret',
                                  'http://127.0.0.1/v1#fragment'])
def test_sensitive_or_ambiguous_base_url_is_rejected(url):
    with pytest.raises(endpoint_client.EndpointError, match='invalid method endpoint URL'):
        endpoint_client.Client(base_url=url, api_key='fixture')


def test_truncated_http_response_does_not_expose_partial_body():
    client = endpoint_client.Client(base_url='http://127.0.0.1:1/v1',
                                   api_key='synthetic-secret', timeout=1)
    class Opener:
        def open(self, *args, **kwargs):
            raise http.client.IncompleteRead(b'synthetic-private-response')
    client.opener = Opener()
    with pytest.raises(endpoint_client.EndpointError) as exc:
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
def test_invalid_usage_cannot_be_recorded_as_normal(endpoint, usage):
    url, requests, plan = endpoint
    caller = model(url)
    plan.append((200, response(usage=usage), {}))
    with pytest.raises(endpoint_client.EndpointError, match='invalid method endpoint response') as exc:
        caller('fixture')
    assert exc.value.response_received and exc.value.attempts == 1
    assert caller.usage() == {'logical_calls': 1, 'transport_attempts': 1, 'calls': []}
    assert len(requests) == 1


def test_invalid_timeout_does_not_echo_credential():
    secret = 'synthetic-credential-as-timeout'
    with pytest.raises(endpoint_client.EndpointError) as exc:
        model('http://127.0.0.1:1/v1', api_key=secret, timeout=secret)
    assert str(exc.value) == 'method endpoint timeout must be finite and positive'
    assert secret not in str(exc.value)


def test_header_construction_exception_is_sanitized(monkeypatch):
    secret = 'synthetic-request-secret'
    client = endpoint_client.Client(base_url='http://127.0.0.1:1/v1', api_key=secret)
    def broken(*args, **kwargs):
        raise ValueError(secret)
    monkeypatch.setattr(endpoint_client, 'Request', broken)
    with pytest.raises(endpoint_client.EndpointError) as exc:
        client.complete(model='fixture', temperature=0, messages=[])
    assert secret not in str(exc.value)
    assert str(exc.value) == 'invalid method endpoint request after 1 attempt(s)'


@pytest.mark.parametrize('message', [{'content': None}, {}])
def test_null_or_missing_content_preserves_observed_usage(endpoint, message):
    url, requests, plan = endpoint
    usage = {'prompt_tokens': 4, 'completion_tokens': 2,
             'prompt_tokens_details': {'cached_tokens': None}}
    plan.append((200, json.dumps({'choices': [{'message': message}], 'usage': usage}).encode(), {}))
    caller = model(url)
    with pytest.raises(ValueError, match='model reply must be one JSON object'):
        caller('fixture')
    assert len(requests) == 1
    assert caller.usage()['calls'] == [{'prompt_tokens': 4, 'completion_tokens': 2,
        'transport_attempts': 1, 'unknown_transport_attempts': 0}]


def test_retry_after_and_explicit_no_retry(endpoint, monkeypatch):
    url, requests, plan = endpoint
    waits = []
    monkeypatch.setattr(endpoint_client.time, 'sleep', waits.append)
    plan.extend([(429, b'private', {'Retry-After': '1.5'}), (200, response(), {})])
    assert model(url)('fixture') == {'no_change': 'synthetic'}
    assert waits == [1.5] and len(requests) == 2
    plan.append((503, b'private', {'x-should-retry': 'false'}))
    with pytest.raises(endpoint_client.EndpointError, match='HTTP 503 after 1 attempt'):
        model(url)('fixture')
    assert len(requests) == 3 and waits == [1.5]
    plan.append((429, b'private', {'Retry-After': '60'}))
    with pytest.raises(endpoint_client.EndpointError, match='retry delay exceeds limit'):
        model(url)('fixture')
    assert len(requests) == 4 and waits == [1.5]


def test_experiment_with_only_method_credentials_cannot_post_an_edit(endpoint, tmp_path, monkeypatch):
    from harnessgrad.execution import method_runner
    from native_helpers import fixture as policy_fixture

    url, requests, _ = endpoint
    setup_editor(monkeypatch, url)
    base, req = policy_fixture(tmp_path)
    private = tmp_path / 'private'
    private.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(private))
    monkeypatch.delenv('HG_IMPROVER', raising=False)
    argv = [sys.executable, str(ROOT / 'methods/llm_improver/run.py')]
    work = Path(req['workspace'])
    result = method_runner._call_entrypoint(work, argv, req, timeout_s=30, sandboxed=True,
                                           scratch=tmp_path, cwd=work, readonly=(base,))
    assert not requests
    assert not result['changed']
    assert 'Meta-Harness' in result['method_reported']['error']
    assert result['method_usage']['calls'] == 0
    assert not (work / 'candidate').exists()


def test_large_finite_timeout_remains_supported():
    client = endpoint_client.Client(base_url='http://127.0.0.1:1/v1',
                                   api_key='synthetic', timeout=1801)
    assert client.timeout == 1801


def test_http_failure_leaves_all_attempts_without_usage(endpoint):
    url, requests, plan = endpoint
    caller = model(url)
    plan.extend([(503, b'synthetic-secret-key private body', {})] * 3)
    with pytest.raises(endpoint_client.EndpointError) as exc:
        caller('fixture')
    assert exc.value.attempts == 3 and 'private body' not in str(exc.value)
    assert len(requests) == 3
    assert caller.usage() == {'logical_calls': 1, 'transport_attempts': 3, 'calls': []}


def test_timeout_after_attempt_has_unknown_cost(monkeypatch):
    client = endpoint_client.Client(base_url='http://127.0.0.1:1/v1', api_key='synthetic-secret-key')
    class Opener:
        def open(self, *args, **kwargs):
            raise TimeoutError('synthetic-secret-key private error')
    client.opener = Opener()
    caller = model('http://127.0.0.1:1/v1', client=client)
    with pytest.raises(endpoint_client.EndpointError) as exc:
        caller('fixture')
    assert exc.value.attempts == 3 and 'synthetic-secret-key' not in str(exc.value)
    assert caller.usage() == {'logical_calls': 1, 'transport_attempts': 3, 'calls': []}


@pytest.mark.parametrize('key,value', [('base_url', 'http://user:pass@localhost/v1'),
                                       ('timeout', 'synthetic-invalid-timeout-value'),
                                       ('api_key', 'invalid\nsecret')])
def test_configuration_failure_before_request_has_no_unknown_cost(key, value):
    with pytest.raises(endpoint_client.EndpointError) as exc:
        model('http://127.0.0.1:1/v1', **{key: value})
    assert exc.value.attempts == 0 and value not in str(exc.value)


def test_retried_failure_before_success_marks_partial_usage_unknown(endpoint):
    url, requests, plan = endpoint
    caller = model(url)
    plan.extend([(500, b'private', {}), (200, response(usage={
        'prompt_tokens': 5, 'completion_tokens': 2}), {})])
    assert caller('fixture') == {'no_change': 'synthetic'}
    assert len(requests) == 2
    assert caller.usage() == {'logical_calls': 1, 'transport_attempts': 2, 'calls': [
        {'prompt_tokens': 5, 'completion_tokens': 2, 'transport_attempts': 2,
         'unknown_transport_attempts': 1}]}


@pytest.mark.parametrize('method, replies, changed_file, expected', [
    ('skillopt', [{'edits': [{'path': 'skills/main.md', 'op': 'append',
                              'content': 'Check the result.'}]}] * 3,
     'skills/main.md', 'Check the result.'),
    ('ace', [{'key_insight': 'Run checks', 'bullet_tags': []},
             {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Run tests.'}]}],
     'prompt.md', 'Run tests.'),
    ('evo_harness', [
        {'lesson': 'check status', 'trigger': 'shell', 'evidence': 'failure', 'scope_hint': 'shell'},
        {'skills': [{'path': 'skills/shell/SKILL.md', 'name': 'shell', 'description': 'Shell checks',
                     'source_task_ids': ['t2'], 'content': 'Check exit status.'}]}],
     'skills/shell/SKILL.md', 'Check exit status.'),
])
def test_policy_stages_use_the_selected_cli_in_the_method_sandbox(
        method, replies, changed_file, expected, endpoint, tmp_path, monkeypatch):
    from harnessgrad.execution import method_runner
    from native_helpers import fixture as policy_fixture

    url, requests, _ = endpoint
    setup_editor(monkeypatch, url)
    base, req = policy_fixture(tmp_path)
    work = Path(req['workspace'])
    private = tmp_path / 'private'
    private.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(private))
    monkeypatch.setenv('HG_POLICY_CONFIG', '{"instruction_path":"prompt.md"}')
    for variable in ('HG_IMPROVER', 'HG_IMPROVER_ARGS', 'HG_CODEX_HOME_SOURCE'):
        monkeypatch.delenv(variable, raising=False)

    program = work / 'meta-harness'
    program.write_text(f'#!{sys.executable}\n' + '''import json
from pathlib import Path
root = Path(__file__).parent
count = root / 'count'
index = int(count.read_text()) if count.exists() else 0
replies = json.loads((root / 'replies.json').read_text())
(Path.cwd().parent / 'artifacts' / 'role-result.json').write_text(json.dumps(replies[index]))
count.write_text(str(index + 1))
''')
    program.chmod(0o755)
    (work / 'replies.json').write_text(json.dumps(replies))
    identity = {'name': 'fixture', 'kind': 'cli', 'path': str(program)}
    round_file = base / '_harnessgrad' / 'round.json'
    point = json.loads(round_file.read_text())
    point['identity']['improver'] = identity
    round_file.write_text(json.dumps(point))
    req['improver'] = identity
    argv = [sys.executable, str(ROOT / 'methods' / method / 'run.py')]
    result = method_runner._call_entrypoint(work, argv, req, timeout_s=30, sandboxed=True,
                                           scratch=tmp_path, cwd=work, readonly=(base,),
                                           improver=identity)
    assert '_error' not in result, result
    assert result['changed'], result
    trajectory = json.loads(Path(req['trajectory_out']).read_text())
    candidate = Path(trajectory['steps'][0]['harness_dir'])
    assert expected in (candidate / changed_file).read_text()
    assert int((work / 'count').read_text()) == len(replies)
    assert not requests
