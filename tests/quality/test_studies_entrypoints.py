"""Study tools stay importable and executable without launching a study."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['task_difficulty', 'task_sensitivity'])
def test_study_entrypoint_has_one_owner_and_help_works(name, tmp_path):
    import importlib
    module = importlib.import_module('tools.studies.' + name)
    assert callable(module.main)
    assert module.ROOT == ROOT
    assert not (ROOT / 'tools' / (name + '.py')).exists()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', HG_REVIEWED_ENVIRONMENT_ONLY='1')
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(ROOT / 'tools/studies' / (name + '.py')), '--help'],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr
