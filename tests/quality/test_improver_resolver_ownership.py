"""The improver resolver is shared by the retained CLI and runtime callers."""
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_import_shares_implementation_and_patch_targets(monkeypatch):
    old = importlib.import_module('tools.improver')
    current = importlib.import_module('harnessgrad.improvers.resolver')
    assert old is current and current.ROOT == ROOT
    marker = object()
    monkeypatch.setattr(old, 'candidates', marker)
    assert current.resolve.__globals__['candidates'] is marker


def test_path_loaded_wrapper_and_help_from_outside_checkout(tmp_path):
    path = ROOT / 'tools/improver.py'
    spec = importlib.util.spec_from_file_location('_authored_improver_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.resolve is importlib.import_module('harnessgrad.improvers.resolver').resolve
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr
