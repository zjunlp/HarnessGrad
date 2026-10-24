"""τ³ management limits stay separate from official episode/trial semantics."""
import importlib.util
import json
import sys
from types import ModuleType

import pytest

from data.datasets import tau3_bench as dataset
from harnessgrad.config import reset
from test_tau3_bench_dataset import control_module


@pytest.fixture(autouse=True)
def isolated_limits(monkeypatch):
    monkeypatch.setenv('HG_ENV_MODEL', 'fixture-user')
    monkeypatch.setenv('HG_JUDGE_MODEL', 'fixture-judge')
    monkeypatch.delenv('HG_TAU3_JUDGE_MODEL', raising=False)
    reset()
    yield
    reset()


def test_default_limits_do_not_add_admission_caps():
    tid = 'tau3_bench/retail/24'
    relay = dataset.SETUP[tid]['tool_relay']
    for key in ('max_calls', 'deadline_s', 'max_line_bytes'):
        assert relay[key] is None
    assert relay['record'] == {'path': 'trusted_episode', 'max_bytes': None}
    assert dataset.ENV[tid] == {'kind': 'files', 'network': 'none'}
    assert 'timeout_s' not in dataset.VERIFY[tid]
    for role in ('environment', 'judge'):
        spec = dataset.model_spec(role)
        for key in ('max_output_tokens', 'timeout_s', 'max_attempts',
                    'response_max_bytes', 'max_line_bytes'):
            assert spec[key] is None
        assert spec['max_calls'] == (1 if role == 'judge' else None)
        assert spec['max_retries'] == 0
    assert dataset.TRIAL_PROTOCOL['trials'] == 3
    assert dataset.TRIAL_PROTOCOL['seeds'] == [626729, 373753, 361454]
    row = dataset.roster()[tid]
    private = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    assert (private['max_steps'], private['max_errors']) == (200, 10)


@pytest.mark.parametrize('role', ['environment', 'judge'])
@pytest.mark.parametrize('output_limit', [None, 16384])
def test_optional_output_budget_is_sent_to_real_local_http_broker(monkeypatch, role, output_limit):
    from eval.service.judge import Judge
    from eval.service.tool_transport import call
    with control_module().Endpoint(judge_response='{"results": []}') as endpoint:
        reset()
        spec = dataset.model_spec(role)
        spec['max_output_tokens'] = output_limit
        with Judge(spec, role=role, trial_seed=626729) as broker:
            call(broker.path, 'complete', {'messages': [{'role': 'user', 'content': 'fixture'}]},
                 timeout_s=None, max_line_bytes=None)
            assert not broker.report()['faults']
        assert len(endpoint.requests) == 1
        body = endpoint.requests[0]
        if output_limit is None:
            assert 'max_tokens' not in body
        else:
            assert body['max_tokens'] == output_limit
        if role == 'environment':
            assert body['seed'] == 626729


def test_actor_cli_does_not_reintroduce_default_transport_caps(monkeypatch, capsys):
    transport = ModuleType('tau3_transport')
    calls = []
    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return {'result': 'fixture'}
    transport.call = call
    monkeypatch.setitem(sys.modules, 'tau3_transport', transport)
    path = dataset.ASSETS / 'cli/tau3.py'
    spec = importlib.util.spec_from_file_location('tau3_cli_limits', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, 'argv', ['tau3.py', 'say', 'hello'])
    module.main()
    assert calls == [(('/run/harnessgrad-tool/tool.sock', 'say', {'message': 'hello'}),
                      {'timeout_s': None, 'max_line_bytes': None})]
    assert json.loads(capsys.readouterr().out) == {'result': 'fixture'}


@pytest.mark.parametrize('output_limit', [None, 16384])
def test_platform_control_preserves_resolved_environment_limits(monkeypatch, tmp_path, output_limit):
    original = dataset.model_spec
    def declared_model(role, tools=None):
        spec = original(role, tools)
        if role == 'environment':
            spec.update(max_output_tokens=output_limit, max_calls=3)
        return spec
    monkeypatch.setattr(dataset, 'model_spec', declared_model)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    reset()
    control = control_module()
    with control.Endpoint() as endpoint:
        score, result = control.run(True, True, tmp_path)
    assert score == 1, result
    service = result['runtime_services']['tool_relay']
    assert service['cleanup']['complete']
    assert service['model']['configuration']['max_output_tokens'] == output_limit
    assert service['model']['configuration']['max_calls'] == 3
    assert service['model']['configuration']['timeout_s'] is None
    assert service['model']['configuration']['parameters']['seed'] == 626729
    assert service['receipt']['transport']['limits']['max_calls'] is None
    assert service['receipt']['transport']['limits']['max_line_bytes'] is None
    assert endpoint.requests
    for request in endpoint.requests:
        if output_limit is None:
            assert 'max_tokens' not in request
        else:
            assert request['max_tokens'] == output_limit


def test_environment_request_without_local_timeout_is_cancellable(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from eval.service.judge import Judge
    from eval.service.tool_transport import ToolTransportError, call

    received, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            received.set()
            release.wait(10)  # Fixture cleanup bound, never a runtime policy.
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HG_ENV_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('HG_ENV_API_KEY', '')
    reset()
    try:
        with Judge(dataset.model_spec('environment'), role='environment', trial_seed=626729) as broker:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(call, broker.path, 'complete',
                                      {'messages': [{'role': 'user', 'content': 'fixture'}]},
                                      timeout_s=None, max_line_bytes=None)
                assert received.wait(5)
                process = broker.proc
                assert process is not None
                broker.close()
                with pytest.raises(ToolTransportError):
                    pending.result(timeout=5)
                assert process.poll() is not None
                assert broker.report()['configuration']['timeout_s'] is None
    finally:
        release.set()
        server.shutdown(); server.server_close(); thread.join(timeout=5)
