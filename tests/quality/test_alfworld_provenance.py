"""Source declarations and code roles without ALFWorld data or TextWorld."""
import shutil
import sys

import pytest

from harnessgrad.provenance import measurement_components as identities, scoring_identity
from provenance_test_helpers import copy_profile_sources


def copy_sources(root):
    copy_profile_sources(root, 'alfworld', extra_attributes=())


def test_source_declarations_are_not_verified_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_DATA_ALFWORLD', str(tmp_path / 'no-dataset'))
    value = identities.resolve('alfworld')
    scoring = scoring_identity.resolve('alfworld')
    identities.require_scoring_alignment(scoring, value)
    source = value['data_source']['details']
    assert source['dataset'] == 'alfworld' and source['declared_artifact_sha256'] is None
    assert source['revision'].startswith('archive-declarations-sha256:')
    assert scoring['profile']['metric'] == 'ALFWorld TextWorld replay any-won; max_steps=50'
    assert not (tmp_path / 'no-dataset').exists()
    assert identities.bundle_problems(None, value) and scoring_identity.problems(None, scoring)


def test_loader_scorer_and_shared_code_are_separate(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('alfworld')
    score = scoring_identity.resolve('alfworld')
    paths = tmp_path / 'harnessgrad/paths.py'
    paths.write_bytes(paths.read_bytes() + b'\n# authored loader change\n')
    loader = identities.resolve('alfworld')
    assert loader['loader'] != first['loader'] and loader['scorer'] == first['scorer']
    assert loader['data_source'] == first['data_source'] and scoring_identity.resolve('alfworld') == score
    check = tmp_path / 'data/datasets/alfworld/verify/check.py'
    check.write_bytes(check.read_bytes() + b'\n# authored scorer change\n')
    scorer = identities.resolve('alfworld')
    assert scorer['scorer'] != loader['scorer'] and scorer['loader'] == loader['loader']
    assert scorer['data_source'] == loader['data_source']
    assert scoring_identity.problems(score, scoring_identity.resolve('alfworld'))
    gym = tmp_path / 'data/datasets/alfworld/lib/gym_env.py'
    gym.write_bytes(gym.read_bytes() + b'\n# authored shared change\n')
    shared = identities.resolve('alfworld')
    assert shared['loader'] != scorer['loader'] and shared['scorer'] != scorer['scorer']
    assert shared['data_source'] == scorer['data_source']


def test_archive_declaration_change_is_distinct_from_file_paths(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('alfworld')
    adapter = tmp_path / 'data/datasets/alfworld.py'
    old = '25171f16e20ad7b048c47275c45b0babf3aa1cbab29cec97387922350a9844bc'
    assert old in adapter.read_text()
    adapter.write_text(adapter.read_text().replace(old, 'f' * 64))
    changed = identities.resolve('alfworld')
    assert changed['data_source'] != first['data_source']
    # This adapter owns both loader and verifier declarations, so it is shared.
    assert changed['loader'] != first['loader'] and changed['scorer'] != first['scorer']


def test_invalid_archive_and_loaded_budget_mismatch_fail_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    adapter = tmp_path / 'data/datasets/alfworld.py'
    adapter.write_text(adapter.read_text().replace('25171f16e20ad7b048c47275c45b0babf3aa1cbab29cec97387922350a9844bc', 'invalid'))
    with pytest.raises(ValueError, match='provenance declaration'):
        identities.resolve('alfworld')
    copy_sources(tmp_path)
    monkeypatch.setitem(sys.modules, 'data.datasets.alfworld', type('Loaded', (), {'STEP_BUDGET': 51})())
    with pytest.raises(ValueError, match='step budget differs'):
        identities.resolve('alfworld')


def test_shared_source_change_during_capture_fails_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    capture = identities.capture_implementation

    def mutate(kind, root, components, **kwargs):
        if kind == 'scorer':
            path = root / 'data/datasets/alfworld/lib/gym_env.py'
            path.write_bytes(path.read_bytes() + b'\n# authored concurrent change\n')
        return capture(kind, root, components, **kwargs)

    monkeypatch.setattr(identities, 'capture_implementation', mutate)
    with pytest.raises(ValueError, match='changed during identity capture'):
        identities.resolve('alfworld')


def test_identical_loader_file_relocation_does_not_change_component_identity(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('alfworld')
    original = tmp_path / 'data/scaffolds/declarations.py'
    relocated = tmp_path / 'data/scaffolds/moved_declarations.py'
    shutil.copyfile(original, relocated)
    profile = tmp_path / 'data/datasets/alfworld/measurement_profile.py'
    profile.write_text(profile.read_text().replace('data/scaffolds/declarations.py',
                                                    'data/scaffolds/moved_declarations.py'))
    assert identities.resolve('alfworld') == first
