"""Dataset runtime source relocation preserves identity and isolated imports."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('canonical_first', [True, False])
def test_removed_runtime_import_reports_canonical_path(tmp_path, canonical_first):
    code = """import importlib
if %r:
    importlib.import_module('harnessgrad.datasets.runtime')
try:
    importlib.import_module('harnessgrad.dataset_runtime')
except ImportError as exc:
    assert 'harnessgrad.datasets.runtime' in str(exc)
else:
    raise AssertionError('legacy runtime import unexpectedly succeeded')
""" % canonical_first
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_isolated_registry_import_uses_canonical_runtime(tmp_path):
    code = """import sys
sys.path.insert(0, sys.argv[1])
from data.scaffolds import registry
from harnessgrad.datasets import runtime
assert registry.managed_runtime('gsm8k')
assert runtime.ROOT == __import__('pathlib').Path(sys.argv[1])
"""
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_real_provider_worker_bootstraps_in_isolated_python_without_data(tmp_path):
    code = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module('data.scaffolds.provider_worker',run_name='__main__')")
    env = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT)], input='{}', cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    import json
    response = json.loads(result.stdout)
    assert result.returncode == 1 and response['error']['type'] == 'ValueError'
    assert response['error']['detail'] == 'invalid dataset provider request'
    assert not list(tmp_path.iterdir())
