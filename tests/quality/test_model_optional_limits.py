"""Real local HTTP subprocesses exercise policy, cancellation and attempt evidence."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eval.service.judge import Judge, JudgeUnavailable
from eval.service.tool_transport import call


@pytest.fixture
def local_api(monkeypatch):
    state = {'requests': [], 'codes': [], 'content': 'score', 'entered': threading.Event(),
             'release': threading.Event(), 'block': False, 'invalid': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state['requests'].append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            state['entered'].set()
            if state['block']:
                state['release'].wait(10)
            code = state['codes'].pop(0) if state['codes'] else 200
            result = {'model': 'local-fixture', 'choices': [{'message': {'content': state['content']}}],
                      'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}
            raw = b'invalid JSON private-body' if state['invalid'] else json.dumps(result).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HG_JUDGE_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('HG_JUDGE_API_KEY', 'fixture-private-key')
    yield state
    state['release'].set()
    server.shutdown()
    server.server_close()
    thread.join()


def spec(**kwargs):
    return {'model': 'local-fixture', 'max_line_bytes': None, 'response_max_bytes': None, **kwargs}


def complete(broker):
    return call(broker.path, 'complete', {'messages': [{'role': 'user', 'content': 'fixture'}]},
                timeout_s=10)


@pytest.mark.parametrize('limit', [None, 16])
def test_optional_limits_and_frozen_configuration(local_api, limit):
    declaration = spec(max_output_tokens=limit)
    with Judge(declaration) as broker:
        assert complete(broker)['content'] == 'score'
    payload = local_api['requests'][0]
    assert ('max_tokens' in payload) == (limit is not None)
    if limit is not None:
        assert payload['max_tokens'] == limit
    report = broker.report()
    assert report['configuration'] == declaration
    assert report['attempts'][0]['usage_known']
    assert report['usage_complete']
    assert report['usage'] == {'prompt_tokens': 7, 'completion_tokens': 3}
    assert not report['faults']
    assert 'fixture-private-key' not in json.dumps(report)


def test_omitted_token_budget_does_not_inject_api_default(local_api):
    with Judge(spec()) as broker:
        complete(broker)
    assert 'max_tokens' not in local_api['requests'][0]


def test_recovered_http_attempt_is_not_a_scoring_fault(local_api):
    local_api['codes'] = [503, 429, 200]
    with Judge(spec(max_retries=2, max_calls=1)) as broker:
        complete(broker)
    report = broker.report()
    assert len(report['calls']) == 1
    assert [a['status'] for a in report['attempts']] == ['error', 'error', 'ok']
    assert [a['usage_known'] for a in report['attempts']] == [False, False, True]
    assert report['attempts'][0]['usage'] is None
    assert not report['usage_complete']
    assert report['calls'][0]['status'] == 'ok'
    assert not report['faults']
    assert report['transport']['calls'] == 1
    assert report['usage'] == {'prompt_tokens': 7, 'completion_tokens': 3}


def test_attempt_limit_counts_retries_across_logical_calls(local_api):
    local_api['codes'] = [503, 200, 503, 200]
    with Judge(spec(max_retries=4, max_attempts=3)) as broker:
        complete(broker)
        with pytest.raises(RuntimeError, match='attempt budget exceeded'):
            complete(broker)
    assert len(local_api['requests']) == 3
    assert len(broker.report()['attempts']) == 3
    assert broker.report()['calls'][-1]['status'] == 'error'


@pytest.mark.parametrize('mode', ['bad_request', 'malformed', 'model_output'])
def test_non_transient_failures_are_not_retried(local_api, mode):
    if mode == 'bad_request':
        local_api['codes'] = [400]
    elif mode == 'malformed':
        local_api['invalid'] = True
    else:
        local_api['content'] = None
    with Judge(spec(max_retries=3)) as broker:
        with pytest.raises(RuntimeError):
            complete(broker)
    assert len(local_api['requests']) == 1
    report = broker.report()
    assert report['faults']
    assert 'private-body' not in json.dumps(report)
    if mode == 'model_output':
        assert report['attempts'][0]['usage_known']


def test_legacy_declaration_keeps_zero_retries(local_api):
    local_api['codes'] = [503, 200]
    legacy = {'model': 'local-fixture', 'max_calls': 1, 'max_output_tokens': 16, 'timeout_s': 2}
    with Judge(legacy) as broker:
        with pytest.raises(RuntimeError, match='HTTPError'):
            complete(broker)
    assert len(local_api['requests']) == 1


@pytest.mark.parametrize('limit,success', [(None, True), (8 * 1024 * 1024, False)])
def test_http_response_limit_is_explicitly_removable(local_api, limit, success):
    local_api['content'] = 'x' * (8 * 1024 * 1024)
    with Judge(spec(response_max_bytes=limit)) as broker:
        # Direct invocation isolates the HTTP byte limit from the caller's IPC policy.
        if success:
            result = broker._call('complete', {'messages': [{'role': 'user', 'content': 'fixture'}]})
            assert len(result['content']) == 8 * 1024 * 1024
        else:
            with pytest.raises(JudgeUnavailable, match='ValueError'):
                broker._call('complete', {'messages': [{'role': 'user', 'content': 'fixture'}]})


def test_legacy_declaration_keeps_http_response_limit(local_api):
    local_api['content'] = 'x' * (8 * 1024 * 1024)
    legacy = {'model': 'local-fixture', 'max_calls': 1, 'max_output_tokens': 16, 'timeout_s': 5}
    with Judge(legacy) as broker:
        with pytest.raises(JudgeUnavailable, match='ValueError'):
            broker._call('complete', {'messages': [{'role': 'user', 'content': 'fixture'}]})


def test_close_cancels_request_without_local_timeout(local_api):
    local_api['block'] = True
    errors = []
    broker = Judge(spec(timeout_s=None)).__enter__()

    def invoke():
        try:
            complete(broker)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=invoke)
    thread.start()
    try:
        assert local_api['entered'].wait(5)
        child = broker.proc
        assert child is not None and child.poll() is None
        broker.close()
        thread.join(5)
        assert not thread.is_alive()
        assert child.poll() is not None
        assert broker.directory is None
        assert errors
        assert broker.report()['attempts'][0]['status'] == 'error'
    finally:
        local_api['release'].set()
        broker.close()
        thread.join(5)


def test_explicit_request_timeout_remains_available(local_api):
    local_api['block'] = True
    with Judge(spec(timeout_s=0.1)) as broker:
        with pytest.raises(RuntimeError, match='TimeoutError'):
            complete(broker)
    assert broker.proc is None
    assert broker.report()['attempts'][0]['usage_known'] is False
