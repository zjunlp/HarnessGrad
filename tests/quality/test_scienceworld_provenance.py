"""Public ScienceWorld source declarations without a JVM or task execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from harnessgrad.provenance import measurement_components as identities, scoring_identity
from provenance_test_helpers import copy_profile_sources

ROOT = Path(__file__).resolve().parents[2]


def copy_sources(root):
    copy_profile_sources(root, 'scienceworld', extra_attributes=('FOLD', 'STUDY', 'REQUIREMENTS'))


def test_source_is_a_declaration_not_verified_task_or_jar(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_DATA_SCIENCEWORLD', str(tmp_path / 'no-dataset'))
    value = identities.resolve('scienceworld')
    scoring = scoring_identity.resolve('scienceworld')
    identities.require_scoring_alignment(scoring, value)
    source = value['data_source']['details']
    assert source['dataset'] == 'scienceworld'
    assert source['declared_artifact_sha256'] == 'bec9711010a57240ebc0cf263b7ec1c88bce44b953984c934e709baf0f7ea02d'
    assert source['revision'].startswith('declared-inventory-sha256:')
    assert scoring['profile']['metric'] == 'ScienceWorld final score clamped to 0..100 / 100; max_steps=100'
    assert not (tmp_path / 'no-dataset').exists()
    assert identities.bundle_problems(None, value) and scoring_identity.problems(None, scoring)


def test_source_projection_ignores_comments_but_detects_task_change(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.delitem(sys.modules, 'data.datasets.scienceworld', raising=False)
    first = identities.resolve('scienceworld')
    fold = tmp_path / 'data/datasets/scienceworld/test_fold.json'
    payload = json.loads(fold.read_text())
    payload['about'] = ['authored description only']
    fold.write_text(json.dumps(payload, indent=2))
    assert identities.resolve('scienceworld') == first
    payload['tasks'][0]['variation'] += 100000
    fold.write_text(json.dumps(payload))
    changed = identities.resolve('scienceworld')
    assert changed['data_source'] != first['data_source']
    assert changed['loader'] == first['loader'] and changed['scorer'] == first['scorer']


def test_loader_scorer_and_shared_code_roles(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('scienceworld')
    score = scoring_identity.resolve('scienceworld')
    path = tmp_path / 'harnessgrad/paths.py'
    path.write_bytes(path.read_bytes() + b'\n# authored loader change\n')
    loader = identities.resolve('scienceworld')
    assert loader['loader'] != first['loader'] and loader['scorer'] == first['scorer']
    assert loader['data_source'] == first['data_source'] and scoring_identity.resolve('scienceworld') == score
    check = tmp_path / 'data/datasets/scienceworld/verify/check.py'
    check.write_bytes(check.read_bytes() + b'\n# authored scorer change\n')
    scorer = identities.resolve('scienceworld')
    assert scorer['scorer'] != loader['scorer'] and scorer['loader'] == loader['loader']
    assert scorer['data_source'] == loader['data_source']
    assert scoring_identity.problems(score, scoring_identity.resolve('scienceworld'))
    env = tmp_path / 'data/datasets/scienceworld/lib/scw_env.py'
    env.write_bytes(env.read_bytes() + b'\n# authored shared change\n')
    shared = identities.resolve('scienceworld')
    assert shared['loader'] != scorer['loader'] and shared['scorer'] != scorer['scorer']
    assert shared['data_source'] == scorer['data_source']


def test_conflicting_declarations_and_loaded_cache_fail_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.delitem(sys.modules, 'data.datasets.scienceworld', raising=False)
    study = tmp_path / 'data/datasets/scienceworld/study_300.json'
    payload = json.loads(study.read_text())
    payload['archive']['sha256'] = 'f' * 64
    study.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='provenance declaration'):
        identities.resolve('scienceworld')
    copy_sources(tmp_path)
    monkeypatch.setitem(sys.modules, 'data.datasets.scienceworld', SimpleNamespace(
        STEP_BUDGET=100, _FOLD_CACHE=[{'task_name': 'stale', 'variation': 0, 'gold_moves': 1}],
        _STUDY_CACHE=None))
    with pytest.raises(ValueError, match='fold differs'):
        identities.resolve('scienceworld')


def test_shared_source_change_during_capture_fails_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    capture = identities.capture_implementation
    def mutate(kind, root, components, **kwargs):
        if kind == 'scorer':
            path = root / 'data/datasets/scienceworld/lib/scw_env.py'
            path.write_bytes(path.read_bytes() + b'\n# authored concurrent change\n')
        return capture(kind, root, components, **kwargs)
    monkeypatch.setattr(identities, 'capture_implementation', mutate)
    with pytest.raises(ValueError, match='changed during identity capture'):
        identities.resolve('scienceworld')


def test_identical_loader_relocation_keeps_component_identity(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('scienceworld')
    original = tmp_path / 'data/scaffolds/declarations.py'
    relocated = tmp_path / 'data/scaffolds/moved_declarations.py'
    shutil.copyfile(original, relocated)
    profile = tmp_path / 'data/datasets/scienceworld/measurement_profile.py'
    profile.write_text(profile.read_text().replace('data/scaffolds/declarations.py',
                                                    'data/scaffolds/moved_declarations.py'))
    assert identities.resolve('scienceworld') == first


def test_changed_world_jar_declaration_updates_source_and_shared_code(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.delitem(sys.modules, 'data.datasets.scienceworld', raising=False)
    first = identities.resolve('scienceworld')
    world = tmp_path / 'data/datasets/scienceworld/runtime.py'
    old = 'f4e1fdce0cdabe1bab7128a7e78ea820210d9db3c28d7272bcbbc3f3a1c5f0d7'
    assert old in world.read_text()
    world.write_text(world.read_text().replace(old, 'f' * 64))
    changed = identities.resolve('scienceworld')
    assert changed['data_source'] != first['data_source']
    assert changed['loader'] != first['loader'] and changed['scorer'] != first['scorer']


def test_loaded_study_cache_mismatch_fails_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setitem(sys.modules, 'data.datasets.scienceworld', SimpleNamespace(
        STEP_BUDGET=100, _FOLD_CACHE=None, _STUDY_POOL='test',
        _STUDY_CACHE={'train': [], 'eval': [], 'eval_fold': 'test'}))
    with pytest.raises(ValueError, match='study selection differs'):
        identities.resolve('scienceworld')


@pytest.mark.parametrize('pool', ['', 'dev', 'test'])
def test_loaded_public_fold_and_study_caches_match_profile(tmp_path, pool):
    code = """
import data.datasets.scienceworld as dataset
from harnessgrad.provenance import measurement_components, scoring_identity
assert dataset.test_fold()
components = measurement_components.resolve('scienceworld')
measurement_components.require_scoring_alignment(scoring_identity.resolve('scienceworld'), components)
assert (dataset._STUDY_CACHE is None) == (not bool(dataset._STUDY_POOL))
"""
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1',
           'HG_SCIENCEWORLD_POOL': pool, 'HG_DATA_SCIENCEWORLD': str(tmp_path / 'absent'),
           'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'absent').exists()
