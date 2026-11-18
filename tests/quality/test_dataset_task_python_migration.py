"""The dataset task binding retains its behavior after the module move."""
import importlib

import pytest

from harnessgrad.datasets import task_python


def test_missing_binding_keeps_environment_unchanged():
    env = {'PATH': '/task/bin', 'PYTHONPATH': '/old'}
    assert task_python.validate({'kind': 'files'}) is None
    assert task_python.environment(env, None) is env


def test_bound_environment_masks_python_injection_and_receipt_omits_paths():
    artifact = {'dataset': 'synthetic', 'runtime_id': 'a' * 64,
                'python': '/private/venv/bin/python', 'path': '/private/venv'}
    result = task_python.environment({'PATH': '/bin', 'PYTHONPATH': '/untrusted',
                                      'CONDA_PREFIX': '/untrusted', 'OTHER': 'yes'}, artifact)
    assert result == {'PATH': '/private/venv/bin:/bin', 'OTHER': 'yes',
                      'VIRTUAL_ENV': '/private/venv', 'PYTHONNOUSERSITE': '1',
                      'PYTHONDONTWRITEBYTECODE': '1'}
    assert task_python.receipt(artifact) == {'dataset': 'synthetic', 'runtime_id': 'a' * 64,
                                             'role': 'actor', 'readonly': True}


def test_old_import_refuses_with_new_path():
    with pytest.raises(ImportError, match='harnessgrad.datasets.task_python'):
        importlib.import_module('harnessgrad.task_python')
