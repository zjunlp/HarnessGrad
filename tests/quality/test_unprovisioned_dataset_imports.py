"""Dataset discovery must work before external task pools are installed."""
import importlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_unprovisioned_adapters_import_but_loading_names_the_missing_configuration():
    script = '''
import importlib
from harnessgrad import paths
paths._settings = lambda: {}
for name in ('appworld', 'terminal_bench', 'costbench', 'costbench_dynamic'):
    module = importlib.import_module('data.datasets.' + name)
    if name.startswith('costbench'):
        assert not hasattr(module, 'OFFICIAL_EVAL')
    try:
        module.load()
    except (ValueError, FileNotFoundError) as error:
        assert 'HG_DATA_ROOT' in str(error), (name, str(error))
    else:
        raise AssertionError(name + ' unexpectedly loaded without storage')
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                            env=dict(os.environ), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_terminal_declarations_load_only_when_used_and_cache_results(monkeypatch):
    module = importlib.import_module('data.datasets.terminal_bench')
    calls = []
    monkeypatch.setattr(module, '_declaration_rows', None)
    monkeypatch.setattr(module, '_all', lambda: calls.append(True) or [
        ({'task_id': 'one'}, {'files': []}, {'kind': 'command'}, {'kind': 'exec'})])
    declarations = module._LazyDeclarations(2)
    environments = module._LazyDeclarations(3)
    assert calls == []
    assert declarations['one'] == {'kind': 'command'}
    assert list(declarations) == ['one']
    assert environments['one'] == {'kind': 'exec'}
    assert calls == [True]


def test_appworld_can_load_after_storage_becomes_available_in_same_process():
    script = '''
import os
from harnessgrad import paths
paths._settings = lambda: {}
os.environ['HG_APPWORLD_POOL'] = 'test'
os.environ.pop('HG_APPWORLD_STUDY_IDS', None)
from data.datasets import appworld
assert appworld._POOL_ERROR is not None
appworld._pool = lambda: [
    {'task_id': 'train_1', 'split': 'train', 'instruction': 'study'},
    {'task_id': 'test_1', 'split': 'test_normal', 'instruction': 'exam'},
]
tasks, _ = appworld.load()
assert {task['task_id'] for task in tasks} == {'train_1', 'test_1'}
assert appworld.SPLIT == {'train': ['train_1'], 'eval': ['test_1']}
assert set(appworld.SETUP) == set(appworld.VERIFY) == set(appworld.ENV) == {'train_1', 'test_1'}
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                            env=dict(os.environ), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
