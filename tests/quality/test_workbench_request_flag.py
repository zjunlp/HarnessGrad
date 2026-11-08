"""Authored exception/concurrency controls for per-request assistant sessions."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from console import server, assistant_drivers


def request(body):
    class Fake:
        path = '/api/workbench/edit'
        _api_guard = lambda self: True
        _post_framing = lambda self, **kwargs: True
        _json_body = lambda self: body
        _send = lambda self, payload, code=200: (code, payload)
    return server.Handler.do_POST(Fake())


def test_assistant_exception_cannot_leave_a_request_flag(monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError('authored assistant failure')
    monkeypatch.setattr(server, 'workbench_edit', failure)
    with pytest.raises(RuntimeError):
        request({'name': 'candidate', 'instruction': 'one', 'new_thread': True})
    assert server.force_new_thread() is False


def test_concurrent_requests_receive_their_own_new_thread_value(monkeypatch):
    first_entered, second_entered = threading.Event(), threading.Event()
    values = {}
    def edit(name, instruction, selection='', kind='codex', *, new_thread=False):
        if instruction == 'first':
            first_entered.set()
            assert second_entered.wait(5)
        else:
            assert first_entered.wait(5)
            second_entered.set()
        values[instruction] = new_thread
        return 200, {}
    monkeypatch.setattr(server, 'workbench_edit', edit)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(request, {'name': 'candidate', 'instruction': 'first', 'new_thread': True})
        assert first_entered.wait(5)
        second = workers.submit(request, {'name': 'candidate', 'instruction': 'second', 'new_thread': False})
        assert first.result()[0] == second.result()[0] == 200
    assert values == {'first': True, 'second': False}


def test_explicit_new_thread_does_not_resume_saved_session(tmp_path, monkeypatch):
    root = tmp_path / 'candidates'; candidate = root / 'candidate'; candidate.mkdir(parents=True)
    (candidate / 'harness.json').write_text('{"name":"candidate"}')
    (candidate / 'agent.py').write_text('before=1\n')
    threads = tmp_path / 'threads.json'; threads.write_text('{"candidate":"old-thread"}')
    monkeypatch.setattr(server, 'CANDIDATES', root)
    monkeypatch.setattr(server, 'ASSISTANT_THREADS', threads)
    monkeypatch.setattr(server, 'load_settings', lambda: {'assistant': {}})
    selected = []
    binding = assistant_drivers.AssistantBinding('codex', ('fake-assistant',), 'a' * 64, True,
        details={'codex_home': str(tmp_path / 'codex-home')})
    def invocation(target, prompt, thread, pin):
        selected.append(thread)
        return ['fake-assistant'], prompt
    monkeypatch.setattr(assistant_drivers.AssistantBinding, 'invocation',
                        lambda self, target, prompt, thread, pin: invocation(target, prompt, thread, pin))
    monkeypatch.setattr(server, '_assistant_binding', lambda *args: binding)
    def process(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'thread.started', 'thread_id': 'new-thread'}), stderr='')
    monkeypatch.setattr(server.subprocess, 'run', process)
    code, result = server.workbench_edit('candidate', 'edit', new_thread=True)
    assert code == 200 and result['changed'] == ['agent.py']
    assert selected == [None]
    saved = json.loads(threads.read_text())
    assert saved['candidate'] == 'new-thread'
    assert server.workbench.ThreadStore(threads).session('candidate', binding) == 'new-thread'
