"""Exact managed artifacts and their selected task/role provenance."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data.scaffolds import registry
from harnessgrad import dataset_runtime as runtime
from harnessgrad import dataset_runtime_identity as identity
from tools import prepare_h0
from test_dataset_runtime import environment
from test_dataset_execution_runtime import execution, _unprepared_binding
from test_dataset_provider_protocol import subprocess_provider
from test_python_execution_launch import changed_artifact
from test_prepare_h0 import _write_run


def bindings(marker='a'):
    service = {**_unprepared_binding(), 'runtime_id': marker * 64}
    return {'service': service, 'verifier': {**service, 'role': 'verifier'}}


def test_identity_is_detached_canonical_and_deduplicates_shared_artifacts():
    source = {str(n): bindings() for n in range(1000)}
    value = identity.create(tasks=source)
    assert len(value['artifacts']) == 1
    assert len(value['tasks']) == 1000
    assert identity.validate(value) == value
    assert identity.create(tasks=dict(reversed(list(source.items())))) == value
    source['0']['service']['runtime_paths'].append('/changed')
    assert identity.validate(value) == value
    assert identity.create() is None and identity.fields(None) == {}


def test_projection_keeps_task_roles_and_only_their_artifacts():
    value = identity.create(tasks={'a': bindings(), 'b': bindings('b')})
    assert identity.project(value, ['a']) == identity.create(tasks={'a': bindings()})
    assert identity.project(value, []) is None
    assert identity.project(None, ['a']) is None
    swapped = {'a': {'service': bindings('b')['service']}, 'b': {'service': bindings()['service']}}
    assert identity.create(tasks=swapped) != identity.create(tasks={
        'a': {'service': bindings()['service']}, 'b': {'service': bindings('b')['service']}})


@pytest.mark.parametrize('change', ['version', 'digest', 'artifact', 'role', 'task', 'extra', 'empty'])
def test_malformed_or_tampered_envelopes_are_refused(change):
    value = identity.create(tasks={'task': bindings()})
    if change == 'version':
        value['version'] = True
    elif change == 'digest':
        value['sha256'] = 'b' * 64
    elif change == 'artifact':
        next(iter(value['artifacts'].values()))['runtime_id'] = 'b' * 64
    elif change == 'role':
        value['tasks']['task']['provider'] = value['tasks']['task'].pop('service')
    elif change == 'task':
        value['tasks']['other'] = value['tasks'].pop('task')
    elif change == 'extra':
        value['unused'] = True
    elif change == 'empty':
        value.update(provider=None, artifacts={}, tasks={})
    with pytest.raises(ValueError):
        identity.validate(value)


@pytest.mark.parametrize('left,right,allowed', [
    (None, None, True), ('a', 'a', True), (None, 'a', False), ('a', None, False),
    ('a', 'b', False), ('broken', 'a', False), ('a', 'broken', False)])
def test_comparison_requires_valid_equal_identities(left, right, allowed):
    def value(marker):
        if marker == 'broken':
            return {'sha256': 'invalid'}
        return None if marker is None else identity.create(tasks={'t': bindings(marker)})
    if allowed:
        identity.require_same(value(left), value(right))
    else:
        with pytest.raises(ValueError, match='dataset_runtime_identity'):
            identity.require_same(value(left), value(right))


def test_exact_provider_admission_survives_index_change_but_rejects_tampering(environment):
    _, _, store, _ = environment
    selected = runtime.prepare('control', root=store)
    ready = next(store.glob('*/ready.json'))
    ready.unlink()
    ready.write_text('{}')
    assert runtime.admit_provider('control', selected)['runtime_id'] == selected['runtime_id']
    with pytest.raises(runtime.DatasetRuntimeUnavailable):
        runtime.resolve('control', root=store)
    with changed_artifact(selected):
        with pytest.raises(runtime.DatasetRuntimeUnavailable, match='inventory'):
            runtime.admit_provider('control', selected)


def test_provider_session_freezes_actual_requests_and_resets_afterward(subprocess_provider, monkeypatch):
    client, _, _ = subprocess_provider
    selected = client.catalog()['runtime']
    admission = Mock(side_effect=lambda name, value: dict(value))
    monkeypatch.setattr(runtime, 'admit_provider', admission)
    with registry.provider_session():
        first = registry.provider('gsm8k')
        assert first.freeze_runtime
        assert first.catalog()['runtime'] == selected
        monkeypatch.setattr(runtime, 'resolve', Mock(side_effect=AssertionError('reselected runtime')))
        assert registry.provider('gsm8k') is first
        assert first.catalog()['runtime'] == selected
        assert registry.provider_runtime('gsm8k') == selected
        admission.assert_called_once_with('gsm8k', selected)
        with registry.provider_session():
            assert registry.provider_runtime('gsm8k') is None
        assert registry.provider_runtime('gsm8k') == selected
    assert registry.provider_runtime('gsm8k') is None


def test_capture_requires_this_runs_observed_provider(subprocess_provider):
    with registry.provider_session():
        with pytest.raises(ValueError, match='loading session'):
            identity.capture('gsm8k', ['t'], {}, {})


def test_execution_capture_and_cache_admission_check_exact_bytes(execution, monkeypatch):
    _, _, store, _ = execution
    selected = runtime.prepare_execution('control', root=store)
    service = runtime.bind(selected, 'service')
    verifier = runtime.bind(selected, 'verifier')
    setups = {'t': {'tool_relay': {'python': service['python'], 'python_runtime': service}}}
    verifiers = {'t': {'python_runtime': verifier, 'argv': [verifier['python'], '-I', '-B', 'check.py'],
                       'runtime_paths': verifier['runtime_paths']}}
    monkeypatch.setattr(registry, 'provider', lambda name: None)
    value = identity.capture('control', ['t'], setups, verifiers)
    assert len(value['artifacts']) == 1
    assert identity.for_evaluation(value, [{'task_id': 't'}], setups, verifiers) == value
    with pytest.raises(ValueError, match='declarations'):
        identity.for_evaluation(value, [{'task_id': 't'}], {}, verifiers)
    with changed_artifact(selected):
        with pytest.raises(ValueError, match='artifact unavailable'):
            identity.admit(value)


@pytest.mark.parametrize('name,expected', [('gsm8k', True), ('costbench', True),
    ('costbench_dynamic', True), ('demo', False), ('tau3_bench', False)])
def test_preparation_discovery_is_static(name, expected, monkeypatch):
    monkeypatch.setattr(registry.importlib, 'import_module', Mock(side_effect=AssertionError('imported adapter')))
    assert registry.managed_runtime(name) is expected


def test_h0_configuration_uses_effective_child_storage_and_selection(tmp_path, monkeypatch):
    ids = tmp_path / 'ids.txt'
    ids.write_text('b\na\n')
    value = identity.create(tasks={'a': bindings()})
    resolve = Mock(return_value=value)
    monkeypatch.setattr(identity, 'resolve_h0', resolve)
    args = SimpleNamespace(dataset='control', side='train', study_ids=ids,
                           model='agent', endpoint='endpoint', trials=1, sampling='random_1',
                           harness_path=Path(__file__).resolve().parents[2]/'tests/fixtures/base_harness/loop_plain')
    config = prepare_h0.configuration(args, env={'HG_AGENT_BACKEND': 'mock', 'HG_DATA_ROOT': '/frozen/data'})
    assert config['dataset_runtime_identity'] == value
    assert resolve.call_args.kwargs['task_ids'] == ['b', 'a']
    assert resolve.call_args.kwargs['env']['HG_DATA_ROOT'] == '/frozen/data'
    assert resolve.call_args.kwargs['env']['HG_APPWORLD_STUDY_IDS'] == str(ids)
    assert resolve.call_args.kwargs['sampling'] == 'random_1'


def test_h0_preflight_refuses_missing_preparation_without_creating_a_store(tmp_path):
    store = tmp_path / 'absent-store'
    env = {**os.environ, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
           'HG_DATASET_RUNTIME_ROOT': str(store), 'HG_DATA_GSM8K': str(tmp_path / 'data')}
    with pytest.raises(ValueError, match='Prepare explicitly'):
        identity.resolve_h0('gsm8k', side='train', task_ids=[], env=env)
    assert not store.exists()


def test_unmanaged_h0_discovery_never_starts_a_preflight(monkeypatch):
    monkeypatch.setattr(identity.subprocess, 'run', Mock(side_effect=AssertionError('started preflight')))
    assert identity.resolve_h0('demo', side='train', task_ids=[]) is None


@pytest.mark.parametrize('location', ['configuration', 'payload', 'base', 'metadata', 'curve'])
@pytest.mark.parametrize('damage', ['missing', 'changed', 'malformed'])
def test_h0_lookup_and_verification_require_all_identity_copies(execution, tmp_path, location, damage):
    _, _, store, _ = execution
    selected = runtime.prepare_execution('control', root=store)
    value = identity.create(tasks={'t': {'verifier': runtime.bind(selected, 'verifier')}})
    config = {'dataset': 'synthetic', 'agent_model': 'agent', 'agent_backend': 'mock',
              'endpoint': '', 'sampling': 'all', **identity.fields(value)}
    run = _write_run(tmp_path, 'h0-synthetic-test', per_task={'t': .5}, config=config)
    for filename in ('h0.json', 'run_meta.json', 'curve.jsonl'):
        data = json.loads((run / filename).read_text())
        data.update(identity.fields(value))
        if filename == 'h0.json':
            data['base'].update(identity.fields(value))
        (run / filename).write_text(json.dumps(data))
    assert prepare_h0.find_existing(config, tmp_path)[0] == run
    files = {'configuration': 'run_meta.json', 'payload': 'h0.json',
             'base': 'h0.json', 'metadata': 'run_meta.json', 'curve': 'curve.jsonl'}
    target = run / files[location]
    body = json.loads(target.read_text())
    record = body['h0_configuration'] if location == 'configuration' else body['base'] if location == 'base' else body
    if damage == 'missing':
        record.pop(identity.KEY)
    elif damage == 'changed':
        record[identity.KEY] = identity.create(tasks={'another-task': {'verifier': runtime.bind(selected, 'verifier')}})
    else:
        record[identity.KEY]['sha256'] = 'invalid'
    target.write_text(json.dumps(body))
    before = {path: path.read_bytes() for path in run.iterdir()}
    assert prepare_h0.find_existing(config, tmp_path) is None
    with pytest.raises(SystemExit, match='dataset_runtime_identity'):
        prepare_h0.verify(run, None, 1, configuration=config)
    assert all(path.read_bytes() == content for path, content in before.items())
