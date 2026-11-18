"""Canonical identity imports retain root binding; removed paths refuse safely."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_default_source_roots_are_unchanged():
    root = Path(__file__).resolve().parents[2]
    from harnessgrad.provenance import scoring_identity, verification_context
    assert scoring_identity.PROVIDERS == root / 'data/datasets'
    assert verification_context.ROOT == root


@pytest.mark.parametrize('name', ['identity', 'h0_identity', 'scoring_identity', 'verification_context', 'native_judge_identity'])
@pytest.mark.parametrize('canonical_first', [True, False])
def test_removed_paths_refuse_even_with_canonical_module_loaded(name, canonical_first, tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = '''import importlib
name = %r
if %r:
    importlib.import_module('harnessgrad.provenance.' + name)
try:
    importlib.import_module('harnessgrad.' + name)
except ImportError as exc:
    assert 'harnessgrad.provenance.' + name in str(exc)
else:
    raise AssertionError('old import unexpectedly succeeded')
assert importlib.import_module('harnessgrad.provenance.' + name)
''' % (name, canonical_first)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_isolated_harbor_child_reaches_canonical_judge_validation(tmp_path):
    """Exercise the -I child import route without Harbor, a judge or Docker."""
    root = Path(__file__).resolve().parents[2]
    code = '''import asyncio, sys
sys.path.insert(0, sys.argv[1])
from data.scaffolds.native_harbor_runtime import harbor_entry, python_runtime
python_runtime.admit = lambda *a, **kw: None
try:
    asyncio.run(harbor_entry.run({'case': {'dataset': 'jobbench', 'runtime': {}}}))
except ValueError as exc:
    assert 'judge identity' in str(exc), str(exc)
else:
    raise AssertionError('missing judge identity accepted')
assert not harbor_entry.ADMITTED
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(root)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
