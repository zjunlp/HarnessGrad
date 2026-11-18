"""Diagnostic CLI discovery does not start a probe or install a runtime."""
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['check_first_use', 'probe_tool_calls'])
def test_diagnostic_help_works_outside_checkout(name, tmp_path):
    module = importlib.import_module('tools.diagnostics.' + name)
    assert callable(module.main)
    assert not (ROOT / 'tools' / (name + '.py')).exists()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(ROOT / 'tools/diagnostics' / (name + '.py')), '--help'],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr
