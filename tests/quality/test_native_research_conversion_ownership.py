"""Native preparation CLI and isolated worker share the conversion owner."""
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_import_shares_conversion_globals(monkeypatch):
    old = importlib.import_module('tools.prepare_native_research')
    current = importlib.import_module('data.scaffolds.native_research.conversion')
    assert old is current
    marker = object()
    monkeypatch.setattr(old, '_publish_manifest', marker)
    assert current._prepare.__globals__['_publish_manifest'] is marker


def test_path_loaded_cli_and_isolated_worker_import_without_preparing_data(tmp_path):
    cli = ROOT / 'tools/prepare_native_research.py'
    spec = importlib.util.spec_from_file_location('_authored_native_prepare', cli)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.prepare is importlib.import_module('data.scaffolds.native_research.conversion').prepare
    import runpy
    assert runpy.run_path(str(cli))['main'] is module.main
    env = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    result = subprocess.run([sys.executable, str(cli), '--help'], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr
    worker = ROOT / 'data/scaffolds/native_research/prepare_worker.py'
    result = subprocess.run([sys.executable, '-I', '-B', str(worker)], input='{}', cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0 and 'KeyError' in result.stderr and "'runtime'" in result.stderr
    assert not list(tmp_path.iterdir())
