"""Container overlay import checks accept banners, retain actual import failures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def tool():
    spec = importlib.util.spec_from_file_location('import_output_tool', ROOT / 'tools/configure_harness.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('body,passed,detail', [
    ("print('mini-SWE-agent startup banner')", True, '1 module(s)'),
    ("print('startup banner'); raise RuntimeError('broken ABI fixture')", False, 'RuntimeError: broken ABI fixture'),
    ("raise SystemExit(2)", False, 'SystemExit: 2'),
])
def test_container_import_probe_reads_result_after_real_import(
        tmp_path, monkeypatch, body, passed, detail):
    module = tool()
    (tmp_path / 'PROVENANCE.json').write_text(json.dumps({'packages': {'fixture': {'top_level': ['fixture_module']}}}))
    (tmp_path / 'fixture_module.py').write_text(body)
    monkeypatch.setattr(module.runtime, 'overlay_path', lambda *_: tmp_path)
    monkeypatch.setattr(module.container, 'resolve_digest', lambda *_: 'fixture-image')
    monkeypatch.setattr(module.container, 'image_python', lambda *_: 'python3')
    real_run = subprocess.run

    def execute_probe(argv, **kwargs):
        assert argv[:2] == ['docker', 'run']
        assert '--pull=never' in argv
        assert argv[argv.index('--network') + 1] == 'none'
        return real_run([sys.executable, '-c', argv[-1]], env={**os.environ, 'PYTHONPATH': str(tmp_path)}, **kwargs)

    monkeypatch.setattr(module.subprocess, 'run', execute_probe)
    ok, note = module.verify_one(tmp_path, ('hash', 'requirements.txt'), '3.12', 'fixture-image')
    assert ok is passed, note
    assert detail in note


@pytest.mark.parametrize('stdout,returncode', [
    ('banner only\n', 0), ('HGIMPORTPROBEnull\n', 0),
    ('HGIMPORTPROBE[]\nHGIMPORTPROBE[]\n', 0), ('HGIMPORTPROBE[]\n', 1),
])
def test_incomplete_or_failed_probe_cannot_pass(tmp_path, monkeypatch, stdout, returncode):
    module = tool()
    (tmp_path / 'PROVENANCE.json').write_text(json.dumps({'packages': {'fixture': {'top_level': ['fixture_module']}}}))
    monkeypatch.setattr(module.runtime, 'overlay_path', lambda *_: tmp_path)
    monkeypatch.setattr(module.container, 'resolve_digest', lambda *_: 'fixture-image')
    monkeypatch.setattr(module.container, 'image_python', lambda *_: 'python3')
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs: subprocess.CompletedProcess([], returncode, stdout, ''))
    ok, note = module.verify_one(tmp_path, ('hash', 'requirements.txt'), '3.12', 'fixture-image')
    assert not ok, note
