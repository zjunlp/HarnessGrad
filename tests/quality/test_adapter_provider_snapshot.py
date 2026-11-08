"""Offline controls for generic providers and exact execution selection."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.scaffolds import provider_worker as worker, registry
from harnessgrad import dataset_provider as client, dataset_runtime as runtime
from test_dataset_execution_runtime import execution
from test_dataset_runtime import environment


def test_adapter_descriptor_accepts_native_protocol(environment):
    _, folder, _, _ = environment
    path = folder / 'runtime.json'
    value = json.loads(path.read_text())
    value['provider'] = 'adapter'
    path.write_text(json.dumps(value))
    assert runtime.descriptor('control')['provider'] == 'adapter'


def test_snapshot_ignores_pointer_change_and_private_home(execution, monkeypatch, tmp_path):
    _, _, store, _ = execution
    artifact = runtime.prepare_execution('control', root=store)
    binding = runtime.bind(artifact, 'service')
    publication = next(store.glob('*/ready.json'))
    publication.chmod(0o600)
    publication.write_text('{"instance":"absent","runtime_id":"changed"}')
    monkeypatch.setenv('HOME', str(tmp_path / 'private-home'))
    monkeypatch.delenv('HG_DATASET_RUNTIME_ROOT', raising=False)
    with runtime.execution_snapshot({'control': binding}):
        assert runtime.resolve_execution('control')['runtime_id'] == artifact['runtime_id']
        with pytest.raises(runtime.DatasetRuntimeUnavailable, match='not selected'):
            runtime.resolve_execution('other')
    manifest = Path(artifact['manifest_path'])
    manifest.chmod(0o600)
    manifest.write_text('{}')
    with pytest.raises(runtime.DatasetRuntimeUnavailable):
        with runtime.execution_snapshot({'control': binding}):
            pass


def test_empty_worker_snapshot_never_discovers_missing_artifact(execution):
    _, _, store, calls = execution
    with runtime.execution_snapshot({}):
        with pytest.raises(runtime.DatasetRuntimeUnavailable):
            runtime.resolve_execution('control', root=store)
    assert not store.exists() and not calls


def test_generic_control_keeps_native_oracle_shape(monkeypatch):
    task = {'task_id': 'native/one', 'goal': 'public'}
    value = {'task': task, 'setup': {}, 'verifier': {}, 'env': {},
             'oracle_files': {'.oracle/actions.json': '["private walkthrough"]'}, 'noop_expected': 0}
    seen = []
    module = SimpleNamespace(command_control=lambda *, task_id: (seen.append(task_id) or value))
    monkeypatch.setattr(runtime, 'descriptor', lambda name: {'provider': 'adapter'})
    monkeypatch.setattr(registry, 'execution_runtime_name', lambda name: None)
    monkeypatch.setattr(worker.importlib, 'import_module', lambda name: module)
    result = worker.dispatch({'dataset': 'native', 'operation': 'control', 'task_ids': ['native/one']})
    provider = object.__new__(client.DatasetProvider)
    provider.name, provider.declaration = 'native', {'provider': 'adapter'}
    provider._request = lambda *args, **kwargs: result
    assert provider.control('native/one') == value
    assert seen == ['native/one']
    assert 'private walkthrough' not in json.dumps(result['task'])
    assert 'record' not in result


def test_generic_resolve_requires_parent_selected_execution(monkeypatch):
    monkeypatch.setattr(registry, 'execution_runtime_name', lambda name: 'control')
    with pytest.raises(ValueError, match='selected execution artifact'):
        worker.dispatch({'dataset': 'native', 'operation': 'resolve', 'task_ids': []})


def test_client_freezes_execution_and_checks_worker_echo(execution, monkeypatch, tmp_path):
    import sys
    _, _, store, _ = execution
    artifact = runtime.prepare_execution('control', root=store)
    declaration = {'provider': 'adapter', 'descriptor_sha256': 'provider-descriptor'}
    provider_artifact = {'python': sys.executable, 'path': sys.prefix, 'runtime_id': 'provider-id',
                         'descriptor_sha256': 'provider-descriptor'}
    monkeypatch.setattr(runtime, 'descriptor', lambda name: declaration)
    monkeypatch.setattr(runtime, 'resolve', lambda name: provider_artifact)
    monkeypatch.setattr(runtime, 'admit_provider', lambda name, selected: provider_artifact)
    monkeypatch.setattr(registry, 'execution_runtime_name', lambda name: 'control')
    monkeypatch.setenv('HG_DATA_NATIVE', str(tmp_path / 'data'))
    original = runtime.resolve_execution
    selections = []
    monkeypatch.setattr(runtime, 'resolve_execution',
                        lambda name: (selections.append(name) or original(name, root=store)))
    requests = []
    def run(*args, input_text, **kwargs):
        request = json.loads(input_text)
        requests.append(request)
        response = {key: request[key] for key in
                    ('schema', 'dataset', 'operation', 'descriptor_sha256', 'runtime_id')}
        if 'execution_runtime' in request:
            response['execution_runtime'] = request['execution_runtime']
        response['interpreter'] = {'python': sys.executable, 'prefix': sys.prefix}
        response['result'] = {'tasks': [], 'scorable': {}, 'metadata': {},
                              'setups': {}, 'verifiers': {}, 'envs': {}}
        return SimpleNamespace(stdout=json.dumps(response), returncode=0)
    monkeypatch.setattr(client, 'run', run)
    provider = client.DatasetProvider('native', freeze_runtime=True)
    provider.catalog()
    assert not selections and 'execution_runtime' not in requests[-1]
    provider.resolve([])
    publication = next(store.glob('*/ready.json'))
    publication.chmod(0o600)
    publication.write_text('{}')
    provider.resolve([])
    assert selections == ['control']
    assert requests[-1]['execution_runtime']['runtime_id'] == artifact['runtime_id']
