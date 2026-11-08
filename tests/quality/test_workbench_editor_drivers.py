"""Real binding readers + authored process transcripts; no CLI/agent/model runs."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

import pytest

from console import assistant_drivers as drivers, server, workbench


@pytest.fixture
def installed(tmp_path):
    with tempfile.TemporaryDirectory(prefix='hg-workbench-editors-', dir='/var/tmp') as directory:
        runtime = Path(directory)
        codex = runtime / 'codex/bin/codex'; codex.parent.mkdir(parents=True)
        shutil.copyfile('/bin/true', codex); codex.chmod(0o755)
        node = runtime / 'node/bin/node'; node.parent.mkdir(parents=True)
        shutil.copyfile('/bin/true', node); node.chmod(0o755)
        dsh = runtime / 'dsh'; cli = dsh / 'apps/cli/lib/bin.js'
        cli.parent.mkdir(parents=True); cli.write_text('// authored, never executed\n')
        dependency = dsh / 'dependency.js'; dependency.write_text('// first bytes\n')
        farm = (Path(__file__).resolve().parents[2] / 'base_harness/deepseek-harness/dsh_farm.tsv').read_text()
        for line in farm.splitlines():
            (dsh / line.split('\t')[1]).mkdir(parents=True, exist_ok=True)
        launcher = runtime / 'dsh-improver'; launcher.write_text('#!/bin/sh\nexit 99\n'); launcher.chmod(0o755)
        home = tmp_path / 'dsh-home'; (home / 'profiles').mkdir(parents=True)
        (home / 'profiles/headless.yaml').write_text('[]\n')
        (home / '.credentials.yaml').write_text('fixture credential\n')
        codex_home = tmp_path / 'codex-home'; codex_home.mkdir()
        env = {'PATH': os.defpath, 'HOME': str(tmp_path / 'user'), 'CODEX_HOME': str(codex_home), 'DEEPSEEK_API_KEY': 'declared-dsh-key'}
        resolved = {
            'codex': {'ok': True, 'kind': 'cli', 'path': str(codex), 'name': 'codex', 'version': 'fixture'},
            'dsh': {'ok': True, 'kind': 'cli', 'path': str(launcher), 'name': 'dsh', 'argv_style': 'dsh',
                    'home': str(home), 'model': 'editor-model', 'provider': 'deepseek-official',
                    'profile': 'headless', 'api_key_env': 'DEEPSEEK_API_KEY',
                    'dsh_runtime': {'root': str(dsh), 'node': str(node), 'entrypoint': str(cli)}}}
        def resolve(kind, environment, settings):
            return drivers.resolve(kind, environment, settings,
                registry_resolver=lambda selected, model: {**resolved[selected], **({'model': model} if model else {})})
        yield SimpleNamespace(codex=codex, node=node, cli=cli, dependency=dependency,
                              home=home, env=env, resolve=resolve, resolved=resolved)


def make_candidate(root, name='candidate'):
    path = root / name; path.mkdir(parents=True)
    (path / 'harness.json').write_text(json.dumps({'name': name}))
    (path / 'agent.py').write_text('before=1\n')
    return path


def helper(root, installed, process, *, settings=None):
    return workbench.WorkbenchService(root, threads=workbench.ThreadStore(root.parent / 'threads.json'),
        load_settings=lambda: {'assistant': settings or {}}, binding_resolver=installed.resolve,
        run_process=process, snapshot=workbench.snapshot)


def codex_reply(thread='thread-one'):
    return '\n'.join([json.dumps({'type': 'thread.started', 'thread_id': thread}),
                      json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Codex reply'}})])


def test_codex_and_dsh_use_their_own_real_protocols(installed, tmp_path):
    root = tmp_path / 'candidates'; make_candidate(root, 'codex-source'); make_candidate(root, 'dsh-source')
    calls = []
    def process(argv, **kwargs):
        calls.append((argv, kwargs))
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        if argv[0] == str(installed.codex):
            assert argv[1] == 'exec' and '-C' in argv and argv[-1] == '-'
            assert kwargs['input'] == 'edit'
            return SimpleNamespace(returncode=0, stdout=codex_reply(), stderr='')
        assert argv[:2] == [str(installed.node), str(installed.cli)]
        assert '-C' not in argv and '--json' not in argv and argv[-1] == 'edit'
        assert kwargs['input'] is None
        pin = Path(argv[argv.index('--patch') + 1])
        assert pin.is_file() and pin.parent.name.startswith('hg-workbench-dsh-')
        assert not pin.parent.is_relative_to(root)
        assert kwargs['env']['DSH_HOME'] == str(pin.parent / 'home')
        assert (Path(kwargs['env']['DSH_HOME']) / 'profiles/node_modules').is_dir()
        assert not (Path(kwargs['env']['DSH_HOME']) / '.credentials.yaml').exists()
        assert not (Path(kwargs['env']['DSH_HOME']) / 'settings.yaml').exists()
        assert not (Path(kwargs['env']['DSH_HOME']) / 'sessions').exists()
        assert pin.stat().st_mode & 0o777 == 0o600
        assert 'HG_AGENT_API_KEY' not in kwargs['env']
        assert kwargs['env']['DEEPSEEK_API_KEY'] == 'assistant-only'
        return SimpleNamespace(returncode=0, stdout='DSH editor changed the selected file.', stderr='')
    service = helper(root, installed, process, settings={'api_key': 'assistant-only'})
    codex = service.edit(workbench.WorkbenchRequest('codex-source', 'edit', kind='codex'), environment=installed.env)
    dsh = service.edit(workbench.WorkbenchRequest('dsh-source', 'edit', kind='dsh'), environment=installed.env)
    assert codex[0] == dsh[0] == 200
    assert codex[1]['message'] == 'Codex reply' and codex[1]['session'] == 'thread-one'
    assert dsh[1]['message'] == 'DSH editor changed the selected file.' and dsh[1]['session'] is None
    assert dsh[1]['assistant']['kind'] == 'dsh' and not dsh[1]['assistant']['session_supported']
    assert not Path(calls[1][0][calls[1][0].index('--patch') + 1]).exists()
    assert not (root / '.dsh-model-pin.yml').exists()
    assert not list(root.glob('.assistant-dsh-*'))


@pytest.mark.parametrize('kind', drivers.UNAVAILABLE)
def test_unavailable_editors_fail_before_copy_or_process(installed, tmp_path, kind):
    root = tmp_path / 'candidates'; source = make_candidate(root)
    service = helper(root, installed, lambda *args, **kwargs: pytest.fail('unavailable editor executed'))
    code, payload = service.edit(workbench.WorkbenchRequest('candidate', 'edit', kind=kind), environment=installed.env)
    assert code == 503 and payload['error'] == 'assistant-not-configured'
    assert set(root.iterdir()) == {source}


def test_missing_runtime_is_diagnosed_before_copy(installed, tmp_path):
    root = tmp_path / 'candidates'; source = make_candidate(root)
    installed.node.unlink()
    service = helper(root, installed, lambda *args, **kwargs: pytest.fail('missing editor executed'))
    code, payload = service.edit(workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=installed.env)
    assert code == 503 and payload['error'] == 'assistant-not-configured'
    assert set(root.iterdir()) == {source}


def test_legacy_command_override_is_codex_only_and_opaque_does_not_resume(installed):
    wrapper = installed.codex.parent / 'wrapper'; wrapper.write_text('#!/bin/sh\nexit 0\n'); wrapper.chmod(0o755)
    environment = {**installed.env, 'HG_CONSOLE_ASSISTANT_CMD': str(wrapper) + ' --json'}
    codex = installed.resolve('codex', environment, {})
    argv, stdin = codex.invocation(Path('/candidate'), 'prompt', 'foreign-thread', None)
    assert argv[:2] == [str(wrapper), '--json'] and stdin == 'prompt'
    assert not codex.resumable
    dsh = installed.resolve('dsh', environment, {})
    assert dsh.command == (str(installed.node), str(installed.cli))
    with pytest.raises(drivers.AssistantUnavailable, match='registry'):
        installed.resolve('dsh', {**environment, 'HG_CONSOLE_ASSISTANT_DRIVER': 'dsh'}, {})


def test_mismatched_command_protocol_is_not_success(installed, tmp_path):
    root = tmp_path / 'candidates'; make_candidate(root)
    def plain(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout='DSH plain reply is not a Codex JSON transcript', stderr='')
    result = helper(root, installed, plain).edit(workbench.WorkbenchRequest('candidate', 'edit'), environment=installed.env)
    assert result[0] == 502 and result[1]['error'] == 'assistant-protocol'


def test_sessions_scope_binding_runtime_and_legacy_migration(installed, tmp_path):
    store = workbench.ThreadStore(tmp_path / 'sessions.json')
    store.remember('candidate', 'legacy-codex')
    first = installed.resolve('codex', installed.env, {})
    assert first.resumable and store.session('candidate', first) == 'legacy-codex'
    assert store.session('candidate', installed.resolve('dsh', installed.env, {})) is None
    # A different executable at a new physical path cannot claim a legacy session.
    other = installed.codex.parent.parent.parent / 'other/bin/codex'
    other.parent.mkdir(parents=True); shutil.copyfile(installed.codex, other); other.chmod(0o755)
    second = installed.resolve('codex', {**installed.env, 'HG_ASSISTANT_CODEX': str(other)}, {})
    assert second.sha256 != first.sha256 and store.session('candidate', second) is None
    store.remember_session('candidate', second, 'second-thread')
    assert store.session('candidate', first) == 'legacy-codex'
    assert store.session('candidate', second) == 'second-thread'
    assert store.session('candidate', first, new_thread=True) is None
    # Same path but changed runtime bytes produces a different binding.
    original = installed.codex.read_bytes(); installed.codex.write_bytes(original + b'changed')
    third = installed.resolve('codex', installed.env, {})
    assert third.sha256 != first.sha256 and store.session('candidate', third) is None


def test_session_writers_preserve_distinct_binding_scopes(installed, tmp_path):
    store = workbench.ThreadStore(tmp_path / 'sessions.json')
    binding = installed.resolve('codex', installed.env, {})
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda n: store.remember_session(str(n), binding, 'thread-' + str(n)), range(8)))
    assert [store.session(str(n), binding) for n in range(8)] == ['thread-' + str(n) for n in range(8)]


def test_capabilities_match_resolver_and_keep_six_visible_names(installed):
    result = drivers.capabilities(installed.env, {}, binding_resolver=installed.resolve)
    assert len(result['assistants']) == 6
    assert [row['kind'] for row in result['assistants'] if row['supported']] == ['codex', 'dsh']
    assert all(row['configured'] == row['supported'] for row in result['assistants'])
    source = (Path(__file__).resolve().parents[2] / 'console/static/harness.html').read_text()
    for kind in (*drivers.SUPPORTED, *drivers.UNAVAILABLE):
        assert f'value="{kind}"' in source
    for kind in drivers.UNAVAILABLE:
        assert f'value="{kind}" disabled' in source


def test_concurrent_dsh_requests_have_private_homes_and_pins(installed, tmp_path):
    import threading
    root = tmp_path / 'candidates'; make_candidate(root, 'first'); make_candidate(root, 'second')
    pins, homes = [], []
    barrier = threading.Barrier(2)
    def process(argv, **kwargs):
        pin = Path(argv[argv.index('--patch') + 1]); home = Path(kwargs['env']['DSH_HOME'])
        pins.append(pin); homes.append(home)
        barrier.wait(timeout=5)
        assert pin.is_file() and home.is_dir() and not pin.is_relative_to(root)
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout='DSH reply', stderr='')
    service = helper(root, installed, process)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda name: service.edit(
            workbench.WorkbenchRequest(name, 'edit', kind='dsh'), environment=installed.env), ('first', 'second')))
    assert all(result[0] == 200 for result in results)
    assert len(set(pins)) == len(set(homes)) == 2
    assert all(not path.exists() for path in [*pins, *homes])
    assert not (root / '.dsh-model-pin.yml').exists()


def test_dsh_timeout_removes_only_private_runtime_state(installed, tmp_path):
    root = tmp_path / 'candidates'; source = make_candidate(root)
    owned = []
    def timeout(argv, **kwargs):
        owned.append(Path(kwargs['env']['DSH_HOME']).parent)
        raise __import__('subprocess').TimeoutExpired(argv, 1800)
    result = helper(root, installed, timeout).edit(
        workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=installed.env)
    assert result[0] == 504
    assert source.is_dir() and (root / result[1]['candidate']).is_dir()
    assert owned and all(not path.exists() for path in owned)


def test_capabilities_http_uses_the_same_binding_producer(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_assistant_binding', installed.resolve)
    monkeypatch.setattr(server, 'load_settings', lambda: {'assistant': {}})
    monkeypatch.setattr(server.os, 'environ', installed.env)
    class Request:
        path = '/api/workbench/capabilities'
        _api_guard = lambda self: True
        _send = lambda self, payload, status=200: (status, payload)
        _error = lambda self, payload, status=400: (status, payload)
    code, result = server.Handler.do_GET(Request())
    assert code == 200
    assert [row['kind'] for row in result['assistants'] if row['configured']] == ['codex', 'dsh']
    Request.path += '?unexpected=1'
    assert server.Handler.do_GET(Request())[0] == 400


def test_preflight_reads_declarations_without_running_build_or_probe(monkeypatch, installed):
    from tools import improver
    monkeypatch.setattr(improver, 'load_config', lambda: {'improvers': {
        'codex': {'kind': 'cli', 'path': str(installed.codex), 'build_command': ['pnpm', 'build']}}})
    monkeypatch.setattr(improver.subprocess, 'run', lambda *args, **kwargs: pytest.fail('preflight ran a build/probe'))
    result = server._resolve_assistant_registry('codex', '')
    assert result['ok'] and result['read_only'] and result['path'] == str(installed.codex)


def test_readonly_canonical_dsh_projection_runs_no_process(installed, monkeypatch):
    from tools import improver
    from harnessgrad import config
    for name in ('HG_DSH_ROOT', 'HG_DSH_NODE', 'HG_IMPROVER_DSH_HOME'):
        monkeypatch.delenv(name, raising=False)
    config.reset()
    entry = {'name': 'dsh', 'kind': 'cli', 'argv_style': 'dsh',
        'launcher': str(Path(__file__).resolve().parents[2] / 'methods/dsh-improver'),
        'root': str(installed.cli.parents[3]), 'interpreter': str(installed.node),
        'home': str(installed.home), 'provider': 'deepseek-official', 'model': 'editor-model',
        'profile': 'headless', 'api_key_env': 'DEEPSEEK_API_KEY',
        'runtime': ['{root}', '{home}'], 'build_command': ['pnpm', 'build']}
    monkeypatch.setattr(improver.subprocess, 'run', lambda *args, **kwargs: pytest.fail('read-only resolver executed a process'))
    before = {p: p.read_bytes() for p in installed.home.rglob('*') if p.is_file()}
    try:
        result = improver.declared_editor_binding('dsh', config={'improvers': {'dsh': entry}}, environment=installed.env)
        assert result['ok'] and result['read_only']
        assert result['dsh_runtime']['node'] == str(installed.node)
        assert all(path.read_bytes() == data for path, data in before.items())
    finally:
        config.reset()


def test_dsh_custom_route_contains_only_selected_config_not_credentials(installed, tmp_path):
    installed.resolved['dsh'].update(base_url='https://editor.invalid/v1', wire='openai-completions',
                                    provider='custom-provider', api_key_env='SELECTED_EDITOR_KEY')
    (installed.home / 'settings.yaml').write_text('unrelated provider and model secret')
    (installed.home / 'profiles/unrelated.yaml').write_text('unrelated profile secret')
    (installed.home / 'sessions').mkdir(); (installed.home / 'sessions/old').write_text('operator session')
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        home = Path(kwargs['env']['DSH_HOME'])
        assert not (home / 'settings.yaml').exists() and not (home / '.credentials.yaml').exists()
        assert not (home / 'sessions').exists() and not (home / 'profiles/unrelated.yaml').exists()
        assert kwargs['env']['SELECTED_EDITOR_KEY'] == 'selected-key'
        assert 'DEEPSEEK_API_KEY' not in kwargs['env']
        pin = Path(argv[argv.index('--patch') + 1]); data = pin.read_text()
        assert 'selected-key' not in data and 'unrelated' not in data
        rows = json.loads(data)
        assert rows[-1]['config'] == {'provider': 'harnessgrad-workbench', 'model': 'editor-model'}
        assert rows[0]['config']['providers']['harnessgrad-workbench']['apiKeyEnv'] == 'SELECTED_EDITOR_KEY'
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout='selected DSH reply', stderr='')
    result = helper(root, installed, process, settings={'api_key': 'selected-key'}).edit(
        workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=installed.env)
    assert result[0] == 200


def test_capabilities_is_readonly_and_diagnostics_do_not_echo_keys(installed, tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *args, **kwargs: pytest.fail('preflight contacted network'))
    monkeypatch.setattr(server.subprocess, 'run', lambda *args, **kwargs: pytest.fail('preflight executed a process'))
    before = sorted(str(p) for p in tmp_path.rglob('*'))
    result = drivers.capabilities(installed.env, {}, binding_resolver=installed.resolve)
    assert sorted(str(p) for p in tmp_path.rglob('*')) == before
    assert len([row for row in result['assistants'] if row['configured']]) == 2
    def unavailable(kind, environment, assistant):
        raise ValueError('failed https://user:secret@provider.invalid/key synthetic-private-key')
    result = drivers.capabilities(installed.env, {'api_key': 'synthetic-private-key'}, binding_resolver=unavailable)
    assert 'synthetic-private-key' not in json.dumps(result) and 'user:secret' not in json.dumps(result)


def test_http_selected_kind_reaches_only_its_driver(installed, tmp_path, monkeypatch):
    root = tmp_path / 'candidates'
    for kind in (*drivers.SUPPORTED, *drivers.UNAVAILABLE):
        make_candidate(root, 'candidate-' + kind)
    monkeypatch.setattr(server, 'CANDIDATES', root)
    monkeypatch.setattr(server, 'ASSISTANT_THREADS', tmp_path / 'threads.json')
    monkeypatch.setattr(server, 'load_settings', lambda: {'assistant': {}})
    monkeypatch.setattr(server, '_assistant_binding', installed.resolve)
    monkeypatch.setattr(server.os, 'environ', installed.env)
    calls = []
    def process(argv, **kwargs):
        calls.append(argv)
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0,
            stdout=codex_reply() if argv[0] == str(installed.codex) else 'DSH editor reply', stderr='')
    monkeypatch.setattr(server.subprocess, 'run', process)
    def request(kind):
        class Fake:
            path = '/api/workbench/edit'
            _api_guard = lambda self: True
            _post_framing = lambda self, **kwargs: True
            _json_body = lambda self: {'name': 'candidate-' + kind, 'kind': kind, 'instruction': 'edit'}
            _send = lambda self, payload, status=200: (status, payload)
        return server.Handler.do_POST(Fake())
    assert request('codex')[0] == request('dsh')[0] == 200
    assert calls[0][0] == str(installed.codex) and calls[1][:2] == [str(installed.node), str(installed.cli)]
    for kind in drivers.UNAVAILABLE:
        code, result = request(kind)
        assert code == 503 and result['error'] == 'assistant-not-configured'
        assert not list(root.glob('candidate-' + kind + '-edit-*'))
    assert len(calls) == 2


def test_runtime_change_during_turn_refuses_bound_session(installed, tmp_path):
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        installed.codex.write_bytes(installed.codex.read_bytes() + b'changed')
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout=codex_reply('must-not-be-saved'), stderr='')
    result = helper(root, installed, process).edit(workbench.WorkbenchRequest('candidate', 'edit'), environment=installed.env)
    assert result[0] == 503 and result[1]['error'] == 'assistant-runtime'
    thread_file = root.parent / 'threads.json'
    assert not thread_file.exists() or 'must-not-be-saved' not in thread_file.read_text()


def test_new_thread_continuation_never_falls_back_to_legacy_session(installed, tmp_path):
    root = tmp_path / 'candidates'; make_candidate(root)
    store = workbench.ThreadStore(root.parent / 'threads.json'); store.remember('candidate', 'old-session')
    calls = []
    def process(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed"}', stderr='')
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout=codex_reply('fresh-session'), stderr='')
    result = helper(root, installed, process).edit(
        workbench.WorkbenchRequest('candidate', 'edit', new_thread=True), environment=installed.env)
    assert result[0] == 200 and result[1]['continued']
    assert result[1]['session'] == 'fresh-session'
    assert len(calls) == 2 and all('resume' not in argv for argv in calls)


@pytest.mark.parametrize('phase', ['failed', 'continuation-timeout'])
def test_failed_turn_is_explicit_and_retains_candidate_identity(installed, tmp_path, monkeypatch, phase):
    import subprocess
    root = tmp_path / 'candidates'; make_candidate(root)
    monkeypatch.setattr(workbench.time, 'strftime', lambda *args: 'fixed')
    turns = []
    def process(argv, **kwargs):
        turns.append(argv)
        if phase == 'failed':
            return SimpleNamespace(returncode=2, stdout='', stderr='wrong CLI protocol synthetic-private-key')
        if len(turns) == 2:
            raise subprocess.TimeoutExpired(argv, 1800)
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed"}', stderr='')
    service = helper(root, installed, process, settings={'api_key': 'synthetic-private-key'})
    result = service.edit(workbench.WorkbenchRequest('candidate', 'edit'), environment=installed.env)
    assert result[0] == (422 if phase == 'failed' else 504)
    assert (root / result[1]['candidate']).is_dir()
    assert 'synthetic-private-key' not in json.dumps(result)
    assert service.edit(workbench.WorkbenchRequest('candidate', 'edit'), environment=installed.env)[0] == 409


def test_opaque_codex_command_keeps_old_sessions_without_silent_resume(installed, tmp_path):
    root = tmp_path / 'candidates'; make_candidate(root)
    wrapper = installed.codex.with_name('wrapper'); wrapper.write_text('#!/bin/sh\nexit 0\n'); wrapper.chmod(0o755)
    environment = {**installed.env, 'HG_CONSOLE_ASSISTANT_CMD': str(wrapper) + ' --json'}
    store = workbench.ThreadStore(root.parent / 'threads.json'); store.remember('candidate', 'old-saved-thread')
    before = store.path.read_bytes()
    calls = []
    def process(argv, **kwargs):
        calls.append(argv)
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout=codex_reply('not-persisted'), stderr='')
    result = helper(root, installed, process).edit(workbench.WorkbenchRequest('candidate', 'edit'), environment=environment)
    assert result[0] == 200 and result[1]['session'] is None
    assert not result[1]['assistant']['session_supported'] and not result[1]['assistant']['session_resumed']
    assert 'kept but not resumed' in result[1]['assistant']['notice']
    assert store.path.read_bytes() == before and 'resume' not in calls[0]


def test_operator_credential_bag_is_not_a_dsh_key_fallback(installed, tmp_path):
    root = tmp_path / 'candidates'; source = make_candidate(root)
    environment = {key: value for key, value in installed.env.items() if key != 'DEEPSEEK_API_KEY'}
    assert (installed.home / '.credentials.yaml').is_file()
    service = helper(root, installed, lambda *args, **kwargs: pytest.fail('unconfigured DSH editor executed'))
    code, body = service.edit(workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=environment)
    assert code == 503 and 'API key' in body['detail']
    assert set(root.iterdir()) == {source}


def test_selected_credential_is_not_echoed_to_workbench_ui(installed, tmp_path):
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout='edited selected-secret-key', stderr='selected-secret-key')
    result = helper(root, installed, process, settings={'api_key': 'selected-secret-key'}).edit(
        workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=installed.env)
    assert result[0] == 200 and 'selected-secret-key' not in json.dumps(result)
    assert result[1]['message'] == 'edited ***'


def test_short_placeholder_key_does_not_rewrite_codex_json_protocol(installed, tmp_path):
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=0, stdout=codex_reply(), stderr='')
    result = helper(root, installed, process, settings={'api_key': 'a'}).edit(
        workbench.WorkbenchRequest('candidate', 'edit'), environment=installed.env)
    assert result[0] == 200 and result[1]['session'] == 'thread-one'
    assert result[1]['message'] == 'Codex reply'
