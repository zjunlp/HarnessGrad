"""Release recipes are bounded real-method invocations, not GSM8K/mock aliases.

The native BBEH-shaped rows below validate preparation and preflight only. They
are authored instrument fixtures, never reported benchmark/model measurements.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds.native_qa.prepare import prepare
from harnessgrad.experiments import load, to_argv

RECIPES = {
    'bbeh': 'bbeh',
    'terminal-bench2': 'terminal_bench',
    'deepsearchqa': 'deepsearchqa',
    'swe-bench-pro-v2': 'swe_bench_pro',
}


def command_flags():
    tree = ast.parse((ROOT / 'driver.py').read_text())
    return {arg.value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'add_argument'
        for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}


@pytest.mark.parametrize('slug,dataset', RECIPES.items())
def test_recipe_pair_has_one_genuine_method_and_an_unchanged_exam_protocol(slug, dataset):
    train = load(ROOT / f'experiments/rsi-{slug}.json')
    exam = load(ROOT / f'experiments/rsi-{slug}-eval.json')
    assert train['dataset'] == exam['dataset'] == dataset
    assert train['harness'] == exam['harness'] == 'loop'
    assert train['method'] == 'dgm'
    manifest = json.loads((ROOT / 'methods' / train['method'] / 'method.json').read_text())
    assert manifest['kind'] == 'published'
    assert 'method' not in exam  # A saved checkpoint is measured, never edited on exam.
    assert train['side'] == 'train' and exam['side'] == 'eval'
    assert train['split'] == exam['split'] == f'splits/{slug}.json'
    assert train['sampling'] == exam['sampling'] == 'random_5'
    assert train['rounds'] == exam['from_round'] == 1
    assert exam['rounds'] == 0 and exam['from_run'] == train['run_id']
    assert train['jobs'] == exam['jobs'] == train['trials'] == exam['trials'] == 1
    assert train['fail_on_incomplete'] is True and exam['fail_on_incomplete'] is True
    flags = command_flags()
    for config in (train, exam):
        emitted = to_argv(config)
        assert '--fail-on-incomplete' in emitted
        assert {value for value in emitted if value.startswith('--')} <= flags


def test_recipe_document_uses_existing_provisioners_and_declares_heavy_routes():
    document = (ROOT / 'docs/rsi-experiments.md').read_text()
    for source in ('tools/prepare_native_research.py',
                   'tools/configure_harness.py', 'tools/import_env.py',
                   'data/scaffolds/native_harbor_runtime/prepare_runtime.py',
                   'data/datasets/swe_bench_pro/prepare.py'):
        assert source in document and (ROOT / source).is_file()
    for slug in RECIPES:
        assert f'experiments/rsi-{slug}.json' in document
        assert f'experiments/rsi-{slug}-eval.json' in document
    assert 'custom' in document.lower() and 'five held-out tasks' in document
    assert 'prepared-host recipe' in document
    assert '2026-09-22' in document


def test_bbeh_prepare_split_and_exact_recipe_preflight_without_runtime_or_network(tmp_path):
    source = tmp_path / 'native-control.json'
    source.write_text(json.dumps([{'task': 'release-instrument-control',
        'input': f'Public task {i}: provide the requested marker.',
        'target': f'PRIVATE_CONTROL_REFERENCE_{i}'} for i in range(30)]))
    data = tmp_path / 'prepared'
    prepare('bbeh', data, [f'test={source}'])
    split = tmp_path / 'bbeh.json'
    environment = {**os.environ, 'HG_DATA_BBEH': str(data), 'PYTHONDONTWRITEBYTECODE': '1',
        'HG_AGENT_BACKEND': 'openai', 'HG_AGENT_BASE_URL': 'http://127.0.0.1:1/v1',
        'HG_AGENT_MODEL': 'local-solver-control', 'HG_AGENT_API_KEY': 'private-control-key',
        'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1', 'HG_METHOD_MODEL': 'local-editor-control',
        'HG_METHOD_API_KEY': 'private-editor-key'}
    frozen = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'split', 'create',
        '--dataset', 'bbeh', '--source', 'official-eval', '--train-ratio', '0.2',
        '--seed', '42', '--output', str(split)], cwd=ROOT, env=environment,
        capture_output=True, text=True, timeout=45)
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    receipt = json.loads(frozen.stdout)
    assert (receipt['train'], receipt['eval']) == (6, 24)
    assert receipt['official_eval_reused_for_train'] == 6
    result = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'run', '--config',
        'experiments/rsi-bbeh.json', '--set', f'split={split}', '--set',
        f'runs_root={tmp_path / "runs"}', '--set', f'work_root={tmp_path / "work"}',
        '--check-only'], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ready' in result.stdout and 'custom held-out' in result.stdout
    assert not (tmp_path / 'runs').exists() and not (tmp_path / 'work').exists()
    for secret in ('private-control-key', 'private-editor-key', 'PRIVATE_CONTROL_REFERENCE'):
        assert secret not in result.stdout + result.stderr
