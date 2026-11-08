"""Explicit assistant request, thread and process ports; no model/CLI is run."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

import pytest

from console import workbench


def candidate(root, name):
    path = root / name; path.mkdir(parents=True)
    (path / 'harness.json').write_text(json.dumps({'name': name}))
    (path / 'agent.py').write_text('before=1\n')
    return path


def service(root, thread_store, argv_builder, process):
    class Binding:
        kind, sha256, resumable = 'codex', 'f' * 64, True
        details = {}
        def recheck(self):
            pass
        def summary(self):
            return {'kind': 'codex', 'session_supported': True}
        def execution(self, target, environment, settings):
            return nullcontext((environment, None))
        def invocation(self, target, prompt, thread, pin):
            return argv_builder({}, thread), prompt
        def response(self, done):
            return '', ''
    return workbench.WorkbenchService(root, threads=thread_store,
        load_settings=lambda: {'assistant': {}}, binding_resolver=lambda *args: Binding(),
        run_process=process, snapshot=workbench.snapshot)


def test_thread_file_read_is_side_effect_free_and_write_format_is_preserved(tmp_path):
    path = tmp_path / 'missing' / 'threads.json'
    store = workbench.ThreadStore(path)
    assert store.read() == {} and not path.parent.exists()
    store.remember('candidate', '会话')
    assert path.read_text() == json.dumps({'candidate': '会话'}, ensure_ascii=False, indent=1) + '\n'
    assert store.read() == {'candidate': '会话'}


def test_concurrent_service_edits_receive_independent_session_decisions(tmp_path):
    candidate(tmp_path, 'first'); candidate(tmp_path, 'second')
    threads = SimpleNamespace(
        session=lambda candidate, binding, new_thread=False: None if new_thread else {'first': 'old-first', 'second': 'old-second'}[candidate],
        remember_session=lambda *args: None)
    barrier = threading.Barrier(2)
    decisions = []
    def argv(env, thread=None):
        barrier.wait(timeout=5)
        decisions.append(thread)
        return ['authored-process']
    def process(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    helper = service(tmp_path, threads, argv, process)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(helper.edit, workbench.WorkbenchRequest('first', 'edit', new_thread=True), environment={})
        second = workers.submit(helper.edit, workbench.WorkbenchRequest('second', 'edit', new_thread=False), environment={})
        assert first.result()[0] == second.result()[0] == 200
    assert set(decisions) == {None, 'old-second'}
    assert (tmp_path / 'first' / 'agent.py').read_text() == 'before=1\n'
    assert (tmp_path / 'second' / 'agent.py').read_text() == 'before=1\n'


def test_timeout_copy_remains_owned_and_same_target_is_not_reused(tmp_path, monkeypatch):
    candidate(tmp_path, 'source')
    monkeypatch.setattr(workbench.time, 'strftime', lambda *args: 'authored-stamp')
    calls = []
    def timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    helper = service(tmp_path, workbench.ThreadStore(tmp_path / 'threads.json'),
                     lambda *args: ['authored-process'], timeout)
    request = workbench.WorkbenchRequest('source', 'edit')
    code, result = helper.edit(request, environment={})
    assert code == 504 and result['candidate'] == 'source-edit-authored-stamp'
    assert (tmp_path / result['candidate'] / 'harness.json').is_file()
    assert helper.edit(request, environment={})[0] == 409
    assert len(calls) == 1


def test_configuration_failure_cleans_only_the_owned_edit_copy(tmp_path, monkeypatch):
    source = candidate(tmp_path, 'source')
    peer = candidate(tmp_path, 'peer')
    def unavailable(*args):
        raise ValueError('authored unavailable CLI')
    helper = service(tmp_path, workbench.ThreadStore(tmp_path / 'threads.json'), unavailable,
                     lambda *args, **kwargs: pytest.fail('unconfigured helper executed'))
    helper.binding_resolver = unavailable
    assert helper.edit(workbench.WorkbenchRequest('source', 'edit'), environment={})[0] == 503
    assert set(tmp_path.iterdir()) == {source, peer}
