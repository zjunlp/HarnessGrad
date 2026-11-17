"""Retired experiment imports refuse safely and the canonical CLI works."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAIRS = (('experiments', 'config'), ('completion', 'completion'), ('splits', 'splits'))


def test_experiment_configuration_keeps_original_root():
    from harnessgrad.experiment import config
    assert config.PLATFORM_ROOT == ROOT


@pytest.mark.parametrize('canonical_first', [True, False])
@pytest.mark.parametrize('old_name,new_name', PAIRS)
def test_removed_imports_refuse_in_fresh_process(old_name, new_name, canonical_first, tmp_path):
    code = '''import importlib
old, new = %r, %r
if %r:
    importlib.import_module('harnessgrad.experiment.' + new)
try:
    importlib.import_module('harnessgrad.' + old)
except ImportError as exc:
    assert 'harnessgrad.experiment.' + new in str(exc)
else:
    raise AssertionError('old import unexpectedly succeeded')
assert importlib.import_module('harnessgrad.experiment.' + new)
''' % (old_name, new_name, canonical_first)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_split_module_help_and_legacy_migration_message(tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
    outputs = []
    for module in ('harnessgrad.splits', 'harnessgrad.experiment.splits'):
        result = subprocess.run([sys.executable, '-m', module, '--help'], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=20)
        outputs.append((result.returncode, result.stdout, result.stderr))
    assert outputs[0][0] != 0 and 'harnessgrad.experiment.splits' in outputs[0][2]
    assert outputs[1][0] == 0 and 'create' in outputs[1][1]
