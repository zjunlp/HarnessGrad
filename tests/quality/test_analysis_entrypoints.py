"""Organized result readers support package imports and direct execution."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['plot_curve', 'results_matrix', 'run_summary', 'evidence_export',
                                  'component_attribution', 'report_appworld', 'report_livemathbench', 'tau3_report'])
def test_canonical_import_owns_the_entrypoint(name):
    import importlib
    canonical = importlib.import_module('tools.analysis.' + name)
    assert callable(canonical.main)
    assert not (ROOT / 'tools' / (name + '.py')).exists()


@pytest.mark.parametrize('name', ['plot_curve', 'results_matrix', 'run_summary', 'evidence_export',
                                  'component_attribution', 'report_appworld', 'report_livemathbench', 'tau3_report'])
def test_canonical_help_works_outside_checkout(name, tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    path = ROOT / 'tools/analysis' / (name + '.py')
    result = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr


def test_canonical_commands_read_authored_results(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    point = {'score': 0.5, 'sampling': {'policy': 'all', 'task_ids': ['a']},
             'cumulative': {'evaluation_trials': 1}}
    (run / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    for name, args in [('plot_curve', [str(run)]), ('run_summary', [str(run), '--dry-run']),
                       ('results_matrix', ['--runs', str(run)])]:
        path = ROOT / 'tools/analysis' / (name + '.py')
        result = subprocess.run([sys.executable, str(path), *args], cwd=tmp_path,
                                env=env, text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, (name, result.stderr)


def test_package_import_works_outside_checkout(tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(ROOT)
    code = ('from tools.analysis import plot_curve, results_matrix, run_summary; '
            'assert callable(plot_curve.spark); '
            'assert callable(results_matrix.build_matrix); '
            'assert callable(run_summary.summarise)')
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name', ['plot_curve', 'results_matrix', 'run_summary', 'evidence_export',
                                  'component_attribution', 'report_appworld', 'report_livemathbench', 'tau3_report'])
def test_canonical_path_loading_retains_public_functions(name):
    import importlib.util
    import runpy
    path = ROOT / 'tools/analysis' / (name + '.py')
    assert callable(runpy.run_path(str(path))['main'])
    spec = importlib.util.spec_from_file_location('_path_loaded_' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert callable(module.main)
        if name == 'run_summary':
            from harnessgrad.evidence_reports import run_summary
            assert module.main is run_summary.main
        else:
            assert module.main.__globals__ is module.__dict__
    finally:
        del sys.modules[spec.name]


def test_path_loaded_livemath_report_resolves_vendored_metrics():
    import runpy
    values = runpy.run_path(str(ROOT / 'tools/analysis/report_livemathbench.py'))
    point = {'per_task': {'t': .5}, 'per_task_trials': {'t': [1, 0]}, 'n_invalid': 0}
    result = values['aggregate'](point, n=2, ks=(1,))
    assert result['complete'] and result['metrics']['accuracy'] == .5


def test_tau3_report_preserves_complete_and_missing_trial_semantics(monkeypatch):
    from tools.analysis import tau3_report as canonical
    roster = {'synthetic-task': {'domain': 'synthetic'}}
    monkeypatch.setattr(canonical.tau3_bench, 'roster', lambda: roster)
    seeds = canonical.tau3_bench.TRIAL_PROTOCOL['seeds']
    manifest = [{'task_id': 'synthetic-task', 'trial_id': i, 'seed': seed}
                for i, seed in enumerate(seeds)]
    results = [{**row, 'status': 'measured', 'score': score}
               for row, score in zip(manifest, (1, 1, 0))]
    report = canonical.report({'manifest': manifest, 'results': results})
    assert report['complete'] and report['groups']['synthetic'] == {
        '1': pytest.approx(2 / 3), '2': pytest.approx(1 / 3), '3': 0.0}
    assert not report['official_full_base']
    incomplete = canonical.report({'manifest': manifest, 'results': results[:-1]})
    assert not incomplete['complete'] and incomplete['metrics'] is None


def test_run_summary_cli_and_driver_share_module_state(monkeypatch):
    import importlib
    import driver
    from harnessgrad.orchestration import run_completion
    canonical = importlib.import_module('harnessgrad.evidence_reports.run_summary')
    legacy = importlib.import_module('tools.analysis.run_summary')
    assert legacy is canonical is driver.run_summary is run_completion.run_summary
    marker = object()
    monkeypatch.setattr(legacy, 'summarise', marker)
    assert driver.run_summary.summarise is marker and run_completion.run_summary.summarise is marker
