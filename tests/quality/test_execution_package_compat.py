"""Retired execution imports cannot fall through to another checkout."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('name', ['budget_dispatch', 'budget_integration', 'bubblewrap', 'slots', 'run_namespace', 'candidate_runtime'])
@pytest.mark.parametrize('canonical_first', [True, False])
def test_removed_execution_imports_refuse_in_fresh_process(name, canonical_first, tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = '''import importlib
name = %r
if %r:
    importlib.import_module('harnessgrad.execution.' + name)
try:
    importlib.import_module('harnessgrad.' + name)
except ImportError as exc:
    assert 'harnessgrad.execution.' + name in str(exc)
else:
    raise AssertionError('old import unexpectedly succeeded')
assert importlib.import_module('harnessgrad.execution.' + name)
''' % (name, canonical_first)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
