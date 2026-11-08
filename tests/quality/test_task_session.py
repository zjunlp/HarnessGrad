"""Task-scoped acquired handles survive later faults without taking unrelated ownership."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval.run import runner, task_session
from eval.run.task_workspace import TaskWorkspace
from eval.service.native_model_route import NativeModelRoute


def test_service_network_is_borrowed_and_not_recreated(monkeypatch):
    workspace = TaskWorkspace()
    session = task_session.TaskSession(workspace)
    handle = SimpleNamespace(network='borrowed', recordings={})
    monkeypatch.setattr(task_session.services, 'start', lambda *a, **kw: handle)
    monkeypatch.setattr(task_session.services, 'create_network',
                        lambda *a: pytest.fail('borrowed service network was replaced'))
    try:
        session.start_services([], recordings_root=workspace.path, run_id='r', task_id='t', run_seed='0')
        session.ensure_network(run_id='r', task_id='t')
        assert session.services is handle and session.network_name == 'borrowed'
        assert not session.owns_network
    finally:
        workspace.remove_task()


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_later_web_fault_closes_actual_owned_service_process_and_only_owned_network(tmp_path, monkeypatch, error_type):
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text('{"entrypoint":"agent.py"}')
    (repo / 'agent.py').write_text('# authored; never launched\n')
    retained = repo / '.state'
    retained.mkdir()
    (retained / 'sentinel').write_text('retained checkpoint')
    process = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(60)'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    events, workspaces = [], []
    failure = error_type('authored web acquisition fault')

    class Services:
        network = None
        recordings = {}

        def stop(self):
            events.append('service-stop')
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            return None

    def fail_web(**kwargs):
        events.append('web-constructor')
        raise failure

    def workspace():
        value = TaskWorkspace()
        workspaces.append(value)
        return value

    monkeypatch.setattr(runner, 'TaskWorkspace', workspace)
    monkeypatch.setattr(runner, '_refuse_unobserved_policies', lambda *a, **kw: None)
    monkeypatch.setattr(task_session.services, 'start', lambda *a, **kw: Services())
    monkeypatch.setattr(task_session.services, 'create_network', lambda *a: events.append('network-create') or 'owned')
    monkeypatch.setattr(task_session.services, 'gateway_ip', lambda *a: '127.0.0.1')
    monkeypatch.setattr(task_session.services, 'remove_network', lambda name: events.append(('network-remove', name)))
    monkeypatch.setattr(runner.web_gateway_mod, 'WebGateway', fail_web)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    try:
        if error_type is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt) as raised:
                runner.run_one(repo, {'task_id': 'authored'}, env={'kind': 'files',
                    'services': [{'name': 'authored'}], 'web': {'fetch': True}})
            assert raised.value is failure
        else:
            result = runner.run_one(repo, {'task_id': 'authored'}, env={'kind': 'files',
                'services': [{'name': 'authored'}], 'web': {'fetch': True}})
            assert result['invalid']['stage'] == 'start' and result['invalid']['service'] == 'web'
        assert process.poll() is not None
        assert events == ['network-create', 'web-constructor', ('network-remove', 'owned'), 'service-stop']
        assert workspaces and not workspaces[0].path.exists()
        assert (retained / 'sentinel').read_text() == 'retained checkpoint'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_fault_after_actual_native_route_acquisition_closes_route_and_stepwise_directory(tmp_path, monkeypatch):
    repo = tmp_path / 'work/workspace'
    repo.mkdir(parents=True)
    (repo / 'harness.json').write_text('{"entrypoint":"agent.py"}')
    (repo / 'agent.py').write_text('# never launched\n')
    routes, route_directories, workspaces = [], [], []
    failure = KeyboardInterrupt('authored stepwise constructor fault')

    class Route(NativeModelRoute):
        def start(self):
            value = super().start()
            routes.append(self)
            route_directories.append(self.directory)
            return value

    def workspace():
        value = TaskWorkspace()
        workspaces.append(value)
        return value

    monkeypatch.setattr(runner, 'TaskWorkspace', workspace)
    monkeypatch.setattr(runner, 'NativeModelRoute', Route)
    monkeypatch.setattr(runner, '_refuse_unobserved_policies', lambda *a, **kw: None)
    monkeypatch.setattr(runner.chess_stepwise, 'session_for', lambda *a, **kw: object())
    monkeypatch.setattr(runner, 'StepwiseRelay', lambda *a, **kw: (_ for _ in ()).throw(failure))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    # No request is issued: this control acquires only an owned local route/socket.
    monkeypatch.setenv('HG_AGENT_BASE_URL', 'http://127.0.0.1:9/v1')
    with pytest.raises(KeyboardInterrupt) as raised:
        runner.run_one(repo, {'task_id': 'authored'}, env={'kind': 'files', 'network': 'none'})
    assert raised.value is failure and len(routes) == 1
    assert routes[0].directory is None and not route_directories[0].exists()
    assert not workspaces[0].path.exists() and not workspaces[0].stepwise_dir.exists()


def test_fault_after_actual_web_gateway_acquisition_closes_listener_before_owned_network(tmp_path, monkeypatch):
    repo = tmp_path / 'work/workspace'
    repo.mkdir(parents=True)
    (repo / 'harness.json').write_text('{"entrypoint":"agent.py"}')
    (repo / 'agent.py').write_text('# never launched\n')
    gateways, events = [], []
    failure = KeyboardInterrupt('authored container-model constructor fault')
    original_web = runner.web_gateway_mod.WebGateway

    class Web(original_web):
        def start(self):
            value = super().start()
            gateways.append(self)
            events.append('web-start')
            return value

        def stop(self):
            events.append('web-stop')
            super().stop()

    monkeypatch.setattr(runner, '_refuse_unobserved_policies', lambda *a, **kw: None)
    monkeypatch.setattr(runner.web_gateway_mod, 'WebGateway', Web)
    monkeypatch.setattr(task_session.services, 'create_network', lambda *a: events.append('network-create') or 'owned')
    monkeypatch.setattr(task_session.services, 'gateway_ip', lambda *a: '127.0.0.1')
    monkeypatch.setattr(task_session.services, 'remove_network', lambda *a: events.append('network-remove'))
    monkeypatch.setattr(runner.container_mod, 'image_python', lambda *a: '/image/python')
    monkeypatch.setattr(runner.harness_runtime, 'lookup', lambda *a, **kw: None)

    def fail_model(**kwargs):
        events.append('model-constructor-fault')
        raise failure

    monkeypatch.setattr(task_session.modelgate, 'ModelGateway', fail_model)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HG_AGENT_BASE_URL', 'http://127.0.0.1:9/v1')
    with pytest.raises(KeyboardInterrupt) as raised:
        runner.run_one(repo, {'task_id': 'authored'}, recordings_root=tmp_path / 'recordings',
                       env={'kind': 'exec', 'image': 'authored', 'web': {'fetch': True}})
    assert raised.value is failure and len(gateways) == 1
    assert not gateways[0]._thread.is_alive() and gateways[0].server.socket.fileno() == -1
    assert events == ['network-create', 'web-start', 'model-constructor-fault', 'web-stop', 'network-remove']
