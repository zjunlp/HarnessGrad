"""The former record path fails clearly in a fresh Python process."""

import os
from pathlib import Path
import subprocess
import sys


def test_legacy_record_import_reports_current_path(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = "import harnessgrad.records"
    env = dict(os.environ, PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert 'harnessgrad.experiment.records' in result.stderr
