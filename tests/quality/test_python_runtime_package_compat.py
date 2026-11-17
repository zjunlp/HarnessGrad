"""Managed Python runtime uses canonical imports and retains installation roots."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_preparation_root_still_points_to_checkout():
    from harnessgrad.execution.python_runtime import python_harness_runtime
    assert python_harness_runtime.ROOT == Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('canonical_first', [True, False])
@pytest.mark.parametrize('name', ['python_artifacts', 'python_harness_runtime', 'python_exec_runtime'])
def test_removed_imports_refuse_in_fresh_process(name, canonical_first, tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = '''import importlib
name = %r
if %r:
    importlib.import_module('harnessgrad.execution.python_runtime.' + name)
try:
    importlib.import_module('harnessgrad.' + name)
except ImportError as exc:
    assert 'harnessgrad.execution.python_runtime.' + name in str(exc)
else:
    raise AssertionError('old import unexpectedly succeeded')
assert importlib.import_module('harnessgrad.execution.python_runtime.' + name)
''' % (name, canonical_first)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
