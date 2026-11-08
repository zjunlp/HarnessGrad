"""Real loopback listeners and authored lifecycle failures; no backend/model runs."""
import builtins
from types import SimpleNamespace

import pytest

from console import server


def test_main_closes_listener_when_startup_output_fails(tmp_path, monkeypatch):
    created = []
    constructor = server.ConsoleServer
    def create(*args, **kwargs):
        httpd = constructor(*args, **kwargs)
        created.append(httpd)
        return httpd
    monkeypatch.setattr(server, 'ConsoleServer', create)
    monkeypatch.setattr(server, 'RUNS', tmp_path)
    monkeypatch.setattr(server.sys, 'argv', ['console', '--port', '0'])
    def failed_output(*args, **kwargs):
        raise BrokenPipeError('authored closed output')
    monkeypatch.setattr(builtins, 'print', failed_output)
    try:
        with pytest.raises(BrokenPipeError):
            server.main()
        assert len(created) == 1 and created[0].socket.fileno() == -1
    finally:
        for httpd in created:
            httpd.server_close()


def test_repeated_server_close_calls_backend_exactly_once():
    httpd = server.ConsoleServer(('127.0.0.1', 0), server.Handler)
    closed = []
    httpd._submission_service = SimpleNamespace(close=lambda: closed.append(True))
    httpd.server_close(); httpd.server_close()
    assert closed == [True]
    assert httpd.socket.fileno() == -1


def test_backend_close_error_still_closes_listener_once():
    httpd = server.ConsoleServer(('127.0.0.1', 0), server.Handler)
    closed = []
    def close():
        closed.append(True)
        raise RuntimeError('authored backend close failure')
    httpd._submission_service = SimpleNamespace(close=close)
    with pytest.raises(RuntimeError):
        httpd.server_close()
    assert httpd.socket.fileno() == -1
    httpd.server_close()
    assert closed == [True]


def test_submission_backend_cannot_be_created_after_listener_close(tmp_path):
    from console.submission_controller import SubmissionController, SubmissionPaths
    created = []
    def reject(code, detail, field=None):
        raise ValueError(code)
    backend = SimpleNamespace(_reject=reject, SubmissionService=lambda **kwargs: created.append(kwargs))
    httpd = SimpleNamespace(server_address=('127.0.0.1', 0), _console_closed=True)
    with pytest.raises(ValueError, match='service-not-ready'):
        SubmissionController().service(httpd, backend=backend,
            environment={'HG_CONSOLE_SUBMISSIONS_ROOT': str(tmp_path / 'submissions')},
            paths=SubmissionPaths(tmp_path / 'platform', tmp_path / 'runs', tmp_path / 'budget'),
            budget_capacity=lambda: 1, liveness=lambda: (True, {}), env_resolver=lambda *args: {})
    assert not created


def test_close_waits_for_inflight_backend_constructor(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    import threading
    from console.submission_controller import SubmissionController, SubmissionPaths
    controller = SubmissionController()
    monkeypatch.setattr(server, '_submission_controller', controller)
    httpd = server.ConsoleServer(('127.0.0.1', 0), server.Handler)
    entered, release = threading.Event(), threading.Event()
    closed = []
    def construct(**kwargs):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(close=lambda: closed.append(True))
    backend = SimpleNamespace(SubmissionService=construct,
        _reject=lambda *args: pytest.fail('an admitted constructor was unexpectedly refused'))
    def initialize():
        return controller.service(httpd, backend=backend,
            environment={'HG_CONSOLE_SUBMISSIONS_ROOT': str(tmp_path / 'submissions')},
            paths=SubmissionPaths(tmp_path / 'platform', tmp_path / 'runs', tmp_path / 'budget'),
            budget_capacity=lambda: 1, liveness=lambda: (True, {}), env_resolver=lambda *args: {})
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            initializing = workers.submit(initialize)
            assert entered.wait(5)
            closing = workers.submit(httpd.server_close)
            try:
                closing.result(timeout=.1)
            except TimeoutError:
                pass
            release.set()
            initializing.result(timeout=5); closing.result(timeout=5)
        assert closed == [True]
        assert httpd.socket.fileno() == -1
    finally:
        release.set(); httpd.server_close()


@pytest.mark.parametrize('phase', ['init', 'start', 'serve', 'interrupt'])
def test_main_closes_owned_backend_and_listener_on_each_failure(tmp_path, monkeypatch, phase):
    instances, events = [], []
    constructor = server.ConsoleServer
    def create(*args, **kwargs):
        httpd = constructor(*args, **kwargs)
        instances.append(httpd)
        def serve():
            events.append('serve')
            if phase == 'interrupt':
                raise KeyboardInterrupt()
            raise RuntimeError('authored serve failure')
        httpd.serve_forever = serve
        return httpd
    class Backend:
        def __init__(self, **kwargs):
            events.append('init')
            if phase == 'init':
                raise ValueError('authored constructor failure')
        def start(self):
            events.append('start')
            if phase == 'start':
                raise RuntimeError('authored start failure')
        def close(self):
            events.append('close')
    monkeypatch.setattr(server, 'ConsoleServer', create)
    monkeypatch.setattr(server, '_submission_backend', lambda: SimpleNamespace(SubmissionService=Backend))
    monkeypatch.setattr(server, 'RUNS', tmp_path)
    monkeypatch.setattr(server, 'BUDGET_DIR', tmp_path / 'budget')
    monkeypatch.setattr(server, 'BUDGET_CAPACITY', '1')
    monkeypatch.setenv('HG_CONSOLE_SUBMISSIONS_ROOT', str(tmp_path / 'submissions'))
    monkeypatch.setattr(server.sys, 'argv', ['console', '--port', '0'])
    if phase == 'interrupt':
        assert server.main() == 0
    else:
        with pytest.raises(ValueError if phase == 'init' else RuntimeError):
            server.main()
    assert len(instances) == 1 and instances[0].socket.fileno() == -1
    assert events.count('close') == (0 if phase == 'init' else 1)
    instances[0].server_close()
    assert events.count('close') == (0 if phase == 'init' else 1)
