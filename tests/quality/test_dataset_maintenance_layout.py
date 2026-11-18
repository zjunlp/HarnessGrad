"""Dataset maintenance ownership preserves the existing tool entrypoints."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['dataset_classes', 'datasets_doc', 'datasets', 'prepare_dataset', 'split_inventory'])
def test_legacy_tool_import_uses_the_canonical_module(name):
    old = importlib.import_module('tools.' + name)
    current = importlib.import_module('tools.dataset_maintenance.' + name)
    assert old is current and current.ROOT == ROOT


@pytest.mark.parametrize('name', ['dataset_classes', 'dataset_lint', 'datasets_doc', 'new_dataset', 'datasets', 'prepare_dataset', 'split_inventory'])
def test_old_and_new_tool_commands_have_the_same_help(name, tmp_path):
    env = {**os.environ, 'HG_DATA_ROOT': str(tmp_path / 'unused-data'),
           'PYTHONDONTWRITEBYTECODE': '1'}
    paths = [ROOT / 'tools' / (name + '.py'),
             ROOT / 'tools' / 'dataset_maintenance' / (name + '.py')]
    results = [subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=30) for path in paths]
    assert results[0].returncode == results[1].returncode == 0
    assert results[0].stdout == results[1].stdout
    assert results[0].stderr == results[1].stderr == ''


@pytest.mark.parametrize('name,args,expected', [
    ('datasets', ['--list', '--dataset', 'gsm8k', '--json'], 0),
    ('datasets', ['--check', '--dataset', 'gsm8k'], 1),
    ('prepare_dataset', ['--dataset', 'not_a_dataset_fixture', '--runtime', 'execution',
                         '--check', '--json'], 2),
])
def test_old_and_new_read_only_commands_match(name, args, expected, tmp_path):
    env = {**os.environ, 'HG_DATA_ROOT': str(tmp_path / 'absent-data'),
           'HG_DATASET_RUNTIME_ROOT': str(tmp_path / 'absent-runtime'),
           'PYTHONDONTWRITEBYTECODE': '1', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    paths = [ROOT / 'tools' / (name + '.py'),
             ROOT / 'tools' / 'dataset_maintenance' / (name + '.py')]
    results = [subprocess.run([sys.executable, str(path), *args], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=30) for path in paths]
    assert results[0].returncode == results[1].returncode == expected
    assert results[0].stdout == results[1].stdout
    assert results[0].stderr == results[1].stderr
    assert not (tmp_path / 'absent-data').exists()
    assert not (tmp_path / 'absent-runtime').exists()


@pytest.mark.parametrize('name', ['dataset_classes', 'dataset_lint', 'datasets_doc', 'new_dataset',
                                   'datasets', 'prepare_dataset', 'split_inventory'])
def test_old_and_new_module_commands_have_the_same_help(name, tmp_path):
    env = {**os.environ, 'HG_DATA_ROOT': str(tmp_path / 'unused-data'),
           'PYTHONDONTWRITEBYTECODE': '1', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    modules = ['tools.' + name, 'tools.dataset_maintenance.' + name]
    results = [subprocess.run([sys.executable, '-m', module, '--help'], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=30) for module in modules]
    assert results[0].returncode == results[1].returncode == 0
    assert results[0].stdout == results[1].stdout
    assert results[0].stderr == results[1].stderr == ''


@pytest.mark.parametrize('name', ['dataset_classes', 'datasets_doc',
                                   'datasets', 'prepare_dataset', 'split_inventory'])
def test_path_loaded_tool_uses_the_checkout_root(name, tmp_path, monkeypatch):
    import runpy
    monkeypatch.setenv('HG_DATA_ROOT', str(tmp_path / 'unused-data'))
    module = runpy.run_path(str(ROOT / 'tools' / (name + '.py')))
    assert module['ROOT'] == ROOT and callable(module['main'])
    assert not (tmp_path / 'unused-data').exists()


@pytest.mark.parametrize('name', ['new_dataset', 'dataset_lint'])
def test_retired_import_and_path_load_explain_migration(name):
    import runpy
    target = 'tools.dataset_maintenance.' + name
    with pytest.raises(ImportError, match=target):
        importlib.import_module('tools.' + name)
    with pytest.raises(ImportError, match=target):
        runpy.run_path(str(ROOT / 'tools' / (name + '.py')))
