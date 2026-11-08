"""Admission-race negatives for config custody and diagnostic redaction."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from console import assistant_drivers as drivers, workbench
from test_workbench_editor_drivers import installed, make_candidate, codex_reply


@pytest.mark.parametrize('mutation', ['changed', 'created', 'deleted', 'retargeted', 'directory'])
@pytest.mark.parametrize('phase', ['before-spawn', 'after-spawn'])
def test_codex_config_identity_is_rechecked_without_rebinding(installed, tmp_path, mutation, phase):
    config = Path(installed.env['CODEX_HOME']) / 'config.toml'
    if mutation != 'created':
        config.write_text('model = "authored-a"\n')
    admitted = installed.resolve('codex', installed.env, {})
    root = tmp_path / 'candidates'; make_candidate(root)
    threads = workbench.ThreadStore(tmp_path / 'threads.json')
    threads.remember_session('candidate', admitted, 'thread-for-model-a')
    calls, snapshots = [], []
    def mutate():
        if mutation in ('changed', 'created'):
            config.write_text('model = "authored-b"\n')
        elif mutation == 'deleted':
            config.unlink()
        elif mutation == 'retargeted':
            other = config.with_name('other.toml'); other.write_text(config.read_text())
            config.unlink(); config.symlink_to(other)
        else:
            config.unlink(); config.mkdir()
    def snapshot(target):
        result = workbench.snapshot(target)
        snapshots.append(target)
        if len(snapshots) == 1 and phase == 'before-spawn':
            mutate()
        return result
    def process(argv, **kwargs):
        calls.append(argv)
        assert 'thread-for-model-a' in argv
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        if phase == 'after-spawn':
            mutate()
        return SimpleNamespace(returncode=0, stdout=codex_reply('must-not-be-saved'), stderr='')
    # Freeze the original binding. Resolving again would hide the admission race.
    service = workbench.WorkbenchService(root, threads=threads, load_settings=lambda: {'assistant': {}},
        binding_resolver=lambda *args: admitted, run_process=process, snapshot=snapshot)
    status, payload = service.edit(workbench.WorkbenchRequest('candidate', 'edit'), environment=installed.env)
    assert status == 503 and payload['error'] == 'assistant-runtime'
    assert len(calls) == (0 if phase == 'before-spawn' else 1)
    assert 'must-not-be-saved' not in threads.path.read_text()
    assert threads.session('candidate', admitted) == 'thread-for-model-a'


@pytest.mark.parametrize('prefix', [0, 790, 799, 800, 1000])
@pytest.mark.parametrize('source', ['settings', 'environment'])
def test_reason_redacts_complete_secret_before_output_truncation(prefix, source):
    secret = 'AUTHORED_PRIVATE_TOKEN_907xyz'
    reason = 'x' * prefix + secret + ' trailing text'
    environment = {'OPENAI_API_KEY': secret} if source == 'environment' else {}
    settings = {'api_key': secret} if source == 'settings' else {}
    output = drivers._reason(reason, environment, settings)
    assert secret not in output and secret[:10] not in output
    assert output == ('x' * prefix + '*** trailing text')[:800]
    assert len(output) <= 800


@pytest.mark.parametrize('operation', ['capabilities', 'edit'])
def test_codex_config_symlink_cycle_is_a_preflight_diagnostic(installed, tmp_path, operation):
    home = Path(installed.env['CODEX_HOME'])
    (home / 'config.toml').symlink_to('alias.toml')
    (home / 'alias.toml').symlink_to('config.toml')
    root = tmp_path / 'candidates'; source = make_candidate(root)
    def forbidden(*args, **kwargs):
        pytest.fail('invalid filesystem binding launched an editor')
    if operation == 'capabilities':
        result = drivers.capabilities(installed.env, {}, binding_resolver=installed.resolve)
        codex = next(row for row in result['assistants'] if row['kind'] == 'codex')
        assert codex['configured'] is False and 'configuration' in codex['detail']
    else:
        service = workbench.WorkbenchService(root, threads=workbench.ThreadStore(tmp_path / 'threads.json'),
            load_settings=lambda: {'assistant': {}}, binding_resolver=installed.resolve,
            run_process=forbidden, snapshot=workbench.snapshot)
        status, payload = service.edit(workbench.WorkbenchRequest('candidate', 'edit'), environment=installed.env)
        assert status == 503 and payload['error'] == 'assistant-not-configured'
    assert set(root.iterdir()) == {source}
