"""Static profile aliases and mocked preparation; no dataset/model/network operations."""
import json
from pathlib import Path

import pytest

from data.scaffolds import registry
from harnessgrad import dataset_runtime
from tools import prepare_dataset


def test_existing_aliases_are_static():
    assert registry.execution_runtime_name('math500') == 'math'
    assert registry.execution_runtime_name('costbench_dynamic') == 'costbench'
    assert registry.execution_runtime_name('unknown_fixture') is None


@pytest.mark.parametrize('requested,expected', [('math500', 'math'), ('costbench_dynamic', 'costbench')])
@pytest.mark.parametrize('check', [False, True])
def test_cli_uses_resolved_profile_and_reports_requested_name(requested, expected, check, monkeypatch, capsys, tmp_path):
    calls = []
    def descriptor(name):
        calls.append(('descriptor', name))
        return {'dataset': name} if name == expected else None
    def prepare(name, **kwargs):
        calls.append(('prepare', name))
        return {'path': '/synthetic/prepared', 'dataset': name}
    def inspect(name, **kwargs):
        calls.append(('check', name))
        # A backend result may contain its own dataset; do not overwrite caller identity.
        return {'present': True, 'status': 'ready', 'scope': 'dataset-execution-runtime',
                'dataset': name, 'runtime': {'path': '/synthetic/prepared', 'dataset': name}}
    monkeypatch.setattr(dataset_runtime, 'execution_descriptor', descriptor)
    monkeypatch.setattr(dataset_runtime, 'prepare_execution', prepare)
    monkeypatch.setattr(dataset_runtime, 'check_execution', inspect)
    argv = ['--dataset', requested, '--runtime', 'execution', '--json', '--runtime-root', str(tmp_path)]
    if check:
        argv.append('--check')
    assert prepare_dataset.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['dataset'] == requested and result['runtime_dataset'] == expected
    assert result['runtime']['dataset'] == expected
    assert calls == ([('descriptor', expected), ('check', expected)] if check else
                     [('descriptor', expected), ('prepare', expected), ('check', expected)])
    assert list(tmp_path.iterdir()) == []


def test_static_declaration_does_not_execute_adapter(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    (root / 'scaffolds').mkdir(parents=True)
    (root / 'datasets').mkdir()
    (root / 'datasets/fixture.py').write_text(
        'raise AssertionError("adapter was executed")\nMANAGED_EXECUTION_RUNTIME = "shared"\n')
    monkeypatch.setattr(registry, '__file__', str(root / 'scaffolds/registry.py'))
    assert registry.execution_runtime_name('fixture') == 'shared'


@pytest.mark.parametrize('source', ['MANAGED_EXECUTION_RUNTIME = __import__("os").getcwd()',
    'MANAGED_EXECUTION_RUNTIME = "../escape"',
    'MANAGED_EXECUTION_RUNTIME = "one"\nMANAGED_EXECUTION_RUNTIME = "two"'])
def test_invalid_declarations_are_rejected_without_execution(tmp_path, monkeypatch, source):
    root = tmp_path / 'data'
    (root / 'scaffolds').mkdir(parents=True)
    (root / 'datasets').mkdir()
    (root / 'datasets/fixture.py').write_text(source)
    monkeypatch.setattr(registry, '__file__', str(root / 'scaffolds/registry.py'))
    with pytest.raises(ValueError):
        registry.execution_runtime_name('fixture')


@pytest.mark.parametrize('name', ['../math', '/math', 'math;rm', 'Math', '', 'math/other'])
def test_unsafe_names_fail_before_any_profile_access(name, monkeypatch, capsys):
    monkeypatch.setattr(dataset_runtime, 'execution_descriptor',
                        lambda *a: pytest.fail('invalid name reached runtime lookup'))
    assert prepare_dataset.main(['--dataset', name, '--runtime', 'execution', '--check', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'invalid'


def test_unknown_dataset_is_rejected_without_creating_store(tmp_path, capsys):
    store = tmp_path / 'unused-store'
    code = prepare_dataset.main(['--dataset', 'not_a_dataset_fixture', '--runtime', 'execution',
                                 '--check', '--json', '--runtime-root', str(store)])
    result = json.loads(capsys.readouterr().out)
    assert code == 2 and result['status'] == 'invalid'
    assert not store.exists()


def test_real_descriptor_alias_check_is_read_only(tmp_path, capsys):
    store = tmp_path / 'unprepared-store'
    code = prepare_dataset.main(['--dataset', 'math500', '--runtime', 'execution',
                                 '--check', '--json', '--runtime-root', str(store)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result['dataset'] == 'math500' and result['runtime_dataset'] == 'math'
    assert result['status'] != 'ready' and not store.exists()
