"""Jericho archive declaration and scoring roles without ROMs or engine runtime."""
import shutil
import sys
from types import SimpleNamespace

import pytest

from harnessgrad.provenance import measurement_components as identities, scoring_identity
from provenance_test_helpers import copy_profile_sources


def copy_sources(root):
    copy_profile_sources(root, 'jericho', extra_attributes=())


def test_archive_is_a_declaration_not_a_verified_rom(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_DATA_JERICHO', str(tmp_path / 'no-dataset'))
    components = identities.resolve('jericho')
    scoring = scoring_identity.resolve('jericho')
    identities.require_scoring_alignment(scoring, components)
    source = components['data_source']['details']
    assert source['origin'] == 'https://codeload.github.com/BYU-PCCL/z-machine-games/zip/refs/heads/master'
    assert source['revision'] == 'sha256:' + source['declared_artifact_sha256']
    assert source['declared_artifact_sha256'] == '7c3b38d946009c591c3f8e88d87b6a0141f0f701f447b97477162dfc17ff9b2c'
    assert scoring['profile']['metric'] == 'Jericho clamped game score / positive game maximum; zero if maximum nonpositive'
    assert not (tmp_path / 'no-dataset').exists()
    assert identities.bundle_problems(None, components) and scoring_identity.problems(None, scoring)


def test_loader_scorer_and_shared_changes_keep_source_distinct(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('jericho')
    score = scoring_identity.resolve('jericho')
    paths = tmp_path / 'harnessgrad/paths.py'
    paths.write_bytes(paths.read_bytes() + b'\n# authored loader change\n')
    loader = identities.resolve('jericho')
    assert loader['loader'] != first['loader'] and loader['scorer'] == first['scorer']
    assert loader['data_source'] == first['data_source'] and scoring_identity.resolve('jericho') == score
    check = tmp_path / 'data/datasets/jericho/verify/check.py'
    check.write_bytes(check.read_bytes() + b'\n# authored scorer change\n')
    scorer = identities.resolve('jericho')
    assert scorer['scorer'] != loader['scorer'] and scorer['loader'] == loader['loader']
    assert scorer['data_source'] == loader['data_source']
    assert scoring_identity.problems(score, scoring_identity.resolve('jericho'))
    env = tmp_path / 'data/datasets/jericho/lib/jer_env.py'
    env.write_bytes(env.read_bytes() + b'\n# authored shared change\n')
    shared = identities.resolve('jericho')
    assert shared['loader'] != scorer['loader'] and shared['scorer'] != scorer['scorer']
    assert shared['data_source'] == scorer['data_source']


def test_archive_change_and_loaded_mismatch_are_distinct(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.delitem(sys.modules, 'data.datasets.jericho', raising=False)
    first = identities.resolve('jericho')
    path = tmp_path / 'data/datasets/jericho.py'
    old = first['data_source']['details']['declared_artifact_sha256']
    path.write_text(path.read_text().replace(old, 'f' * 64))
    changed = identities.resolve('jericho')
    assert changed['data_source'] != first['data_source']
    assert changed['loader'] != first['loader'] and changed['scorer'] != first['scorer']
    monkeypatch.setitem(sys.modules, 'data.datasets.jericho', SimpleNamespace(
        ARCHIVE_SHA256=old, ARCHIVE_URL=first['data_source']['details']['origin']))
    with pytest.raises(ValueError, match='differs from source'):
        identities.resolve('jericho')


def test_shared_source_change_during_capture_fails_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    capture = identities.capture_implementation
    def mutate(kind, root, components, **kwargs):
        if kind == 'scorer':
            path = root / 'data/datasets/jericho/lib/jer_env.py'
            path.write_bytes(path.read_bytes() + b'\n# authored concurrent change\n')
        return capture(kind, root, components, **kwargs)
    monkeypatch.setattr(identities, 'capture_implementation', mutate)
    with pytest.raises(ValueError, match='changed during identity capture'):
        identities.resolve('jericho')


def test_identical_loader_relocation_preserves_component_identity(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('jericho')
    original = tmp_path / 'data/scaffolds/declarations.py'
    relocated = tmp_path / 'data/scaffolds/moved_declarations.py'
    shutil.copyfile(original, relocated)
    profile = tmp_path / 'data/datasets/jericho/measurement_profile.py'
    profile.write_text(profile.read_text().replace('data/scaffolds/declarations.py',
                                                    'data/scaffolds/moved_declarations.py'))
    assert identities.resolve('jericho') == first
