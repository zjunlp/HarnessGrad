"""CI tool entrypoints work as package imports and direct scripts."""
import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('run_tests', 'run_selected', 'ci_select', 'quality_sweep')


@pytest.mark.parametrize('name', NAMES)
def test_canonical_import_and_path_loaders(name):
    new = importlib.import_module('tools.testing.' + name)
    assert new.ROOT == ROOT
    path = ROOT / 'tools/testing' / (name + '.py')
    spec = importlib.util.spec_from_file_location('_quality_path_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main.__globals__ is module.__dict__ and module.ROOT == ROOT
    assert not (ROOT / 'tools' / (name + '.py')).exists()


@pytest.mark.parametrize('name', NAMES)
def test_help_works_outside_checkout(name, tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    path = ROOT / 'tools/testing' / (name + '.py')
    proc = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0 and 'usage:' in proc.stdout.lower(), proc.stderr
