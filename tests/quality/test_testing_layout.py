"""Test-selection helpers use explicit paths without being collected as tests."""
from pathlib import Path
import os
import subprocess
import sys

from tools.testing import lane, scope

ROOT = Path(__file__).resolve().parents[2]


def test_scope_classifies_filename_and_keeps_core_and_registry_separate(tmp_path, monkeypatch):
    tests = tmp_path / 'tests/quality'
    tests.mkdir(parents=True)
    for name in ('test_alpha_provider.py', 'test_beta_loader.py', 'test_platform.py', 'test_dataset_controls.py'):
        (tests / name).write_text('')
    monkeypatch.setattr(scope, 'ROOT', tmp_path)
    mapping = scope.scope_map(['alpha', 'beta'])
    assert mapping['alpha'] == {'test_alpha_provider.py'}
    assert mapping['beta'] == {'test_beta_loader.py'}
    assert 'test_platform.py' in mapping['core']
    assert 'test_dataset_controls.py' in mapping['registry']
    assert scope.select(['alpha', 'beta'], ['alpha'], include_core=False) == ['test_alpha_provider.py']


def test_lane_selects_changed_test_and_reports_unmatched_code(tmp_path, monkeypatch):
    tests = tmp_path / 'tests/quality'
    tests.mkdir(parents=True)
    target = tests / 'test_widget.py'
    target.write_text('from application.widget import helper\n')
    monkeypatch.setattr(lane, 'ROOT', tmp_path)
    selected, unmatched = lane.plan(['application/widget.py', 'tests/quality/test_widget.py',
                                     'application/absent.py', 'docs/ignored.md'], [target])
    assert target in selected and 'changed itself' in selected[target]
    assert unmatched == ['application/absent.py']


def test_lane_and_runner_start_from_outside_checkout(tmp_path):
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTHONPATH', None)
    for script in (ROOT / 'tools/testing/lane.py', ROOT / 'tools/testing/run_tests.py'):
        result = subprocess.run([sys.executable, str(script), '--help'], cwd=tmp_path,
                                env=env, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
    assert not (ROOT / 'tools/test_scope.py').exists()
    assert not (ROOT / 'tools/test_lane.py').exists()
