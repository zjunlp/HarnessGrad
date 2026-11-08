"""Synthetic runtime selection only: never launch a task, model, Docker or network."""
import hashlib
import importlib.util
import socket
import subprocess
from pathlib import Path

import pytest

from data.scaffolds import registry
from harnessgrad import dataset_runtime, dataset_runtime_identity

ROOT = Path(__file__).resolve().parents[2]


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('adapter', ['costbench', 'costbench_dynamic'])
def test_selected_store_and_republication_update_new_roles_but_not_old_bindings(tmp_path, monkeypatch, adapter):
    def forbidden(*args, **kwargs):
        raise AssertionError('subprocess and network are forbidden')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    root = tmp_path / 'synthetic-data'
    root.mkdir()
    monkeypatch.setenv('HG_DATA_COSTBENCH', str(root))
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', str(tmp_path / 'store-a'))
    import harnessgrad.paths
    # Isolate adapter imports from any installed dataset and from other test sessions.
    monkeypatch.setattr(harnessgrad.paths, 'dataset_root', lambda name: root)
    module = _module(ROOT / 'data/datasets' / (adapter + '.py'), 'synthetic_' + adapter)
    folder = module.PREPARED / 'fixture'
    folder.mkdir(parents=True)
    raw = b'{"synthetic":true}'
    (folder / 'public.json').write_bytes(raw)
    row = {'path': 'fixture', 'public_sha256': hashlib.sha256(raw).hexdigest(),
           'private_sha256': 'c' * 64}
    published = {'value': 'a'}
    calls = []
    def resolve(name):
        assert name == 'costbench'
        store = Path(__import__('os').environ['HG_DATASET_RUNTIME_ROOT'])
        instance = store / ('build-' + published['value'])
        runtime = instance / 'venv'
        calls.append((str(store), published['value']))
        return {'dataset': name, 'profile': 'execution', 'runtime_id': published['value'] * 64,
                'manifest_path': str(instance / 'artifact.json'), 'path': str(runtime),
                'python': str(runtime / 'bin/python'), 'runtime_paths': [str(runtime)]}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', resolve)
    def declarations():
        setup = module._setup('fixture-task', row)
        verifier = module._verify('fixture-task', row)
        registry._check_setup(setup, 'fixture-task', adapter)
        registry._check_verifier(verifier, 'fixture-task', adapter)
        roles = dataset_runtime_identity._bindings(['fixture-task'], {'fixture-task': setup},
                                                    {'fixture-task': verifier})['fixture-task']
        assert roles['service'] == setup['tool_relay']['python_runtime']
        assert roles['verifier'] == verifier['python_runtime']
        return roles
    first = declarations()
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', str(tmp_path / 'store-b'))
    published['value'] = 'b'
    second = declarations()
    assert first['service']['runtime_id'] == 'a' * 64
    assert second['service']['runtime_id'] == second['verifier']['runtime_id'] == 'b' * 64
    assert second['service']['path'].startswith(str(tmp_path / 'store-b'))
    published['value'] = 'd'  # Same store, a newly published artifact.
    third = declarations()
    assert third['service']['runtime_id'] == third['verifier']['runtime_id'] == 'd' * 64
    assert first['service']['runtime_id'] == first['verifier']['runtime_id'] == 'a' * 64
    assert second['service']['runtime_id'] == second['verifier']['runtime_id'] == 'b' * 64
    assert calls == [(str(tmp_path / store), value) for store, value in
                     [('store-a', 'a'), ('store-a', 'a'), ('store-b', 'b'),
                      ('store-b', 'b'), ('store-b', 'd'), ('store-b', 'd')]]
