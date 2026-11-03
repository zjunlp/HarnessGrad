"""The short BYO entry routes really run outside the platform with no model."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_external_method_template_completes_a_real_sandbox_exchange(tmp_path):
    source = tmp_path / 'my-method'
    shutil.copytree(ROOT / 'examples/method-template', source)
    result = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'run', '--config',
        'experiments/quickstart.json', '--method-entrypoint', shlex.join([sys.executable, str(source/'run.py')]),
        '--run-id', 'external-method-smoke', '--runs-root', str(tmp_path/'runs'),
        '--work-root', str(tmp_path/'work'), '--improver', 'none', '--fail-on-incomplete'],
        cwd=ROOT, env={**os.environ, 'HG_AGENT_BACKEND': 'mock', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    run = tmp_path/'runs/external-method-smoke'
    meta = json.loads((run/'run_meta.json').read_text())
    assert meta['completion']['status'] == 'early_stop'
    assert meta['completion']['coverage_complete'] is True
    assert str(source) in meta['method_entrypoint']
    assert (source/'run.py').read_bytes() == (ROOT/'examples/method-template/run.py').read_bytes()


def test_external_python_harness_h0_smoke_requires_no_optimizer(tmp_path):
    source = tmp_path / 'my-harness'
    shutil.copytree(ROOT/'examples/python-react-harness', source)
    result = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'run', '--dataset', 'demo',
        '--sampling', 'all', '--harness-path', str(source), '--rounds', '0', '--improver', 'none',
        '--run-id', 'external-harness-smoke', '--runs-root', str(tmp_path/'runs'),
        '--work-root', str(tmp_path/'work'), '--fail-on-incomplete'], cwd=ROOT,
        env={**os.environ, 'HG_AGENT_BACKEND': 'mock', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    meta = json.loads((tmp_path/'runs/external-harness-smoke/run_meta.json').read_text())
    assert meta['completion']['status'] == 'complete'
    assert meta['completion']['requested_rounds'] == 0
    assert 'needs --method-entrypoint' not in result.stderr
    assert meta['harness_source']['path'] == str(source)
