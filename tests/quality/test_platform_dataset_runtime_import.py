"""Runtime import migration keeps data and scoring evidence distinct from loader changes."""
import json
from pathlib import Path

import pytest

from harnessgrad.provenance import measurement_components, scoring_identity

ROOT = Path(__file__).resolve().parents[2]
BEFORE = json.loads((ROOT / 'tests/fixtures/platform_dataset_runtime_import/before.json').read_text())


@pytest.mark.parametrize('name', sorted(BEFORE))
def test_dataset_roles_reflect_runtime_import_migrations(monkeypatch, name):
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'authored-judge')
    monkeypatch.setenv('HG_JUDGE_BASE_URL', 'http://127.0.0.1:18999/v1')
    current = measurement_components.resolve(name)
    scoring = scoring_identity.resolve(name)
    previous = BEFORE[name]['components']
    assert current['data_source'] == previous['data_source']
    assert current['scorer'] == previous['scorer']
    if name in {'chessbench_puzzles', 'math', 'math500', 'reasoning_gym'}:
        assert current['loader'] != previous['loader']
        assert any('loader identity changed' in problem
                   for problem in measurement_components.bundle_problems(previous, current))
    else:
        assert current['loader'] == previous['loader']
    assert scoring == BEFORE[name]['scoring']
    measurement_components.require_scoring_alignment(scoring, current)
