"""Optional renderers run from their organized location without touching receipts."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['render_trends', 'render_demo_figure'])
def test_canonical_renderer_import(name):
    new = importlib.import_module('tools.visualization.' + name)
    assert callable(new.main)
    assert not (ROOT / 'tools' / (name + '.py')).exists()


@pytest.mark.parametrize('name', ['render_trends', 'render_demo_figure'])
def test_renderer_help_works_outside_checkout(name, tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    path = ROOT / 'tools/visualization' / (name + '.py')
    result = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr


def test_demo_renderer_produces_identical_png_without_touching_receipts(tmp_path):
    pytest.importorskip('matplotlib')
    source = ROOT / '.github/assets/demo-curve.json'
    before = source.read_bytes()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', MPLCONFIGDIR=str(tmp_path / 'mpl'))
    env.pop('PYTHONPATH', None)
    outputs = []
    path = ROOT / 'tools/visualization/render_demo_figure.py'
    for index in range(2):
        output = tmp_path / f'demo-{index}.png'
        proc = subprocess.run([sys.executable, str(path), '--source', str(source), '--output', str(output)],
                              cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
    assert source.read_bytes() == before


def test_trend_renderer_uses_original_repository_root():
    from tools.visualization import render_trends
    assert render_trends.ROOT == ROOT
    assert render_trends.RECEIPT == ROOT / '.github/assets/trends.json'


@pytest.mark.parametrize('name', ['render_trends', 'render_demo_figure'])
def test_canonical_path_loading_retains_public_functions(name):
    import importlib.util
    import runpy
    path = ROOT / 'tools/visualization' / (name + '.py')
    assert callable(runpy.run_path(str(path))['main'])
    spec = importlib.util.spec_from_file_location('_path_loaded_' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert callable(module.main) and module.main.__globals__ is module.__dict__
    finally:
        del sys.modules[spec.name]
