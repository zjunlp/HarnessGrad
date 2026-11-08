"""Synthetic declaration checks: no installed benchmark or model required."""
import hashlib
import json
from pathlib import Path

import pytest

from data.datasets import tau2_bench, tau3_bench
from harnessgrad import dataset_runtime


@pytest.fixture(params=[tau2_bench, tau3_bench])
def selected(request, tmp_path, monkeypatch):
    adapter = request.param
    name = adapter.__name__.rsplit('.', 1)[1]
    root = tmp_path / 'artifact'
    venv = root / 'venv'
    runtime = {'dataset': name, 'profile': 'execution', 'runtime_id': 'a' * 64,
               'manifest_path': str(root / 'artifact.json'), 'path': str(venv),
               'python': str(venv / 'bin/python'), 'runtime_paths': [str(venv)]}
    monkeypatch.delenv('HG_DATA_' + name.upper() + '_RUNTIME', raising=False)
    calls = []
    def resolve(name):
        calls.append(name)
        return runtime
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', resolve)
    monkeypatch.setattr(adapter, 'DATASET_ROOT', tmp_path)
    folder = tmp_path / 'case'
    folder.mkdir()
    raw = json.dumps({'policy': 'synthetic', 'user_tools': []}).encode()
    (folder / 'public.json').write_bytes(raw)
    row = {'path': 'case', 'public_sha256': hashlib.sha256(raw).hexdigest(),
           'private_sha256': 'b' * 64, 'needs_judge': False, 'user_tools': []}
    monkeypatch.setattr(adapter, 'model_spec', lambda *args: {'model': 'fake'})
    return adapter, runtime, calls, row


def test_service_and_scorer_share_selected_identity_without_private_actor_bytes(selected):
    adapter, runtime, calls, row = selected
    setup = adapter._setup('synthetic', row)
    verify = adapter._verify('synthetic', row)
    relay = setup['tool_relay']
    assert relay['python'] == verify['argv'][0] == runtime['python']
    assert verify['argv'][1:3] == ['-I', '-B']
    assert verify['runtime_paths'] == runtime['runtime_paths']
    for role, spec in [('service', relay), ('verifier', verify)]:
        binding = spec['python_runtime']
        assert dataset_runtime.validate_binding(binding, role, runtime['python']) == binding
    assert len(calls) == 2
    assert 'private.json' not in json.dumps(setup['files'])
    assert 'runtime_id' not in json.dumps(setup['files'])


def test_same_path_change_is_not_hidden_by_adapter_cache(selected, monkeypatch):
    adapter, runtime, calls, row = selected
    adapter._verify('synthetic', row)
    def changed(name):
        raise dataset_runtime.DatasetRuntimeUnavailable('same-path package mutation')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', changed)
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='mutation'):
        adapter._verify('synthetic', row)
    assert not adapter.provision_status()['present']


def test_unmanaged_runtime_override_refuses_silent_reuse(selected, monkeypatch, tmp_path):
    adapter, runtime, calls, row = selected
    name = adapter.__name__.rsplit('.', 1)[1]
    monkeypatch.setenv('HG_DATA_' + name.upper() + '_RUNTIME', str(tmp_path / 'old-venv'))
    with pytest.raises(ValueError, match='selected managed execution artifact'):
        adapter._execution_runtime()


def test_execution_profiles_use_existing_exact_locks():
    for name in ('tau2_bench', 'tau3_bench'):
        declaration = dataset_runtime.execution_descriptor(name)
        assert declaration['requirements'] == ('execution-requirements.lock'
                                                if name == 'tau2_bench' else 'requirements.lock')
        assert 'tau2' not in declaration['pins']
        assert declaration['pins']['pydantic']


@pytest.mark.parametrize('half', ['public', 'private'])
def test_tau3_readiness_rechecks_case_bytes_without_disclosing_gold(tmp_path, monkeypatch, half):
    folder = tmp_path / 'case'
    folder.mkdir()
    row = {'path': 'case'}
    for part in ('public', 'private'):
        raw = b'{"sentinel":"PRIVATE-GOLD-SENTINEL"}'
        (folder / (part + '.json')).write_bytes(raw)
        row[part + '_sha256'] = hashlib.sha256(raw).hexdigest()
    (folder / (half + '.json')).write_text('modified')
    def roster():
        return {'synthetic': row}
    roster.cache_clear = lambda: None
    monkeypatch.setattr(tau3_bench, 'DATASET_ROOT', tmp_path)
    monkeypatch.setattr(tau3_bench, 'roster', roster)
    monkeypatch.setattr(tau3_bench, '_execution_runtime', lambda: {})
    state = tau3_bench.provision_status()
    assert not state['present']
    assert 'case digest mismatch' in state['detail']
    assert 'PRIVATE-GOLD-SENTINEL' not in json.dumps(state)


@pytest.mark.parametrize('name', ['tau2_bench', 'tau3_bench'])
def test_prepared_profile_runs_synthetic_scorer_readonly(tmp_path, monkeypatch, name):
    """Opt in explicitly after preparation; this never installs or runs an episode."""
    import os
    store = os.environ.get('HG_TAU_EXECUTION_TEST_STORE')
    if not store:
        pytest.skip('set HG_TAU_EXECUTION_TEST_STORE to an explicitly prepared external store')
    from eval.verification.verifier import _verify
    artifact = dataset_runtime.resolve_execution(name, root=store)
    check = '''import json,os,sys
from pathlib import Path
Path('verifier/reward.txt').write_text('1')
Path('verifier/evidence.json').write_text(json.dumps({'measured': True,
    'python': sys.executable, 'prefix': sys.prefix,
    'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY)}))
'''
    spec = {'kind': 'command', 'argv': [artifact['python'], '-I', '-B', 'check.py'],
            'python_runtime': dataset_runtime.bind(artifact, 'verifier'),
            'runtime_paths': artifact['runtime_paths'],
            'inputs': [{'path': 'check.py', 'content': check}],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    result = _verify(tmp_path, spec, '', sandbox=True, timeout_s=20, env={'kind': 'files'})
    assert result.get('score') == 1, result
    assert result['evidence']['python'] == artifact['python']
    assert result['evidence']['readonly']
