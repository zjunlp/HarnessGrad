"""Harness validation has one implementation across CLI and execution callers."""
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_old_import_and_canonical_implementation_share_identity_and_globals(monkeypatch):
    old = importlib.import_module('tools.validate_harness')
    current = importlib.import_module('harnessgrad.execution.harness_validation')
    assert old is current
    marker = object()
    monkeypatch.setattr(old, '_smoke', marker)
    assert current.validate.__globals__['_smoke'] is marker


def test_path_loaded_cli_and_outside_checkout_help(tmp_path):
    path = ROOT / 'tools/validate_harness.py'
    spec = importlib.util.spec_from_file_location('_authored_harness_validator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.validate is importlib.import_module('harnessgrad.execution.harness_validation').validate
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr
