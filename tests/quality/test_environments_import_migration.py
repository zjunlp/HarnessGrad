"""Removed environment imports direct callers to the orchestration owner."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('canonical_first', [True, False])
def test_removed_import_reports_canonical_path(canonical_first, tmp_path):
    code = f"""
import importlib
if {canonical_first!r}:
    importlib.import_module('harnessgrad.orchestration.environments')
try:
    importlib.import_module('harnessgrad.environments')
except ImportError as exc:
    assert 'harnessgrad.orchestration.environments' in str(exc)
else:
    raise AssertionError('removed environments import unexpectedly succeeded')
"""
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
