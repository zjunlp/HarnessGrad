"""Source-only Reasoning Gym identity controls; no pool or dependency runtime."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from harnessgrad.provenance import measurement_components as identities, scoring_identity

ROOT = Path(__file__).resolve().parents[2]


def copy_sources(root):
    path = ROOT / 'data/datasets/reasoning_gym/measurement_profile.py'
    spec = importlib.util.spec_from_file_location('reasoning_identity_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = set(module.LOADER.values()) | set(module.SCORER.values()) | {
        'data/datasets/reasoning_gym/measurement_profile.py',
        'data/datasets/reasoning_gym/scoring_profile.py'}
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)


def test_historical_combined_identity_is_valid_but_not_equivalent():
    old = json.loads((ROOT / 'tests/fixtures/reasoning_gym_scoring_identity/before.json').read_text())
    assert scoring_identity.validate(old) == old
    current = scoring_identity.resolve('reasoning_gym')
    components = identities.resolve('reasoning_gym')
    assert scoring_identity.problems(old, current)
    assert old['profile']['metric'] == current['profile']['metric']
    assert identities.bundle_problems(None, components)
    identities.require_scoring_alignment(current, components)
    source = components['data_source']['details']
    assert source['origin'] == 'historical pinned pool; acquisition not asserted'
    assert source['revision'] == 'sha256:' + source['declared_artifact_sha256']


def test_loader_and_scorer_roles_reflect_shared_code(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    original = identities.resolve('reasoning_gym')
    scoring = scoring_identity.resolve('reasoning_gym')
    prepare = tmp_path / 'data/datasets/reasoning_gym/prepare.py'
    prepare.write_bytes(prepare.read_bytes() + b'\n# loader control\n')
    loader = identities.resolve('reasoning_gym')
    assert loader['loader'] != original['loader']
    assert loader['scorer'] == original['scorer'] and loader['data_source'] == original['data_source']
    assert scoring_identity.resolve('reasoning_gym') == scoring
    wrapper = tmp_path / 'data/datasets/reasoning_gym/check.py'
    wrapper.write_bytes(wrapper.read_bytes() + b'\n# scorer control\n')
    scorer = identities.resolve('reasoning_gym')
    assert scorer['scorer'] != loader['scorer'] and scorer['loader'] == loader['loader']
    assert scoring_identity.problems(scoring, scoring_identity.resolve('reasoning_gym'))
    adapter = tmp_path / 'data/datasets/reasoning_gym.py'
    adapter.write_bytes(adapter.read_bytes() + b'\n# shared code control\n')
    shared = identities.resolve('reasoning_gym')
    assert shared['loader'] != scorer['loader'] and shared['scorer'] != scorer['scorer']
    assert shared['data_source'] == scorer['data_source']


def test_pool_pin_change_is_separate_declaration_but_shared_adapter_changes(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('reasoning_gym')
    old = first['data_source']['details']['declared_artifact_sha256']
    for relative in ('data/datasets/reasoning_gym.py', 'data/datasets/reasoning_gym/prepare.py'):
        file = tmp_path / relative
        file.write_text(file.read_text().replace(old, 'f' * 64))
    after = identities.resolve('reasoning_gym')
    assert first['data_source'] != after['data_source']
    assert first['loader'] != after['loader'] and first['scorer'] != after['scorer']


@pytest.mark.parametrize('relative', ['data/datasets/reasoning_gym.py', 'data/datasets/reasoning_gym/prepare.py'])
def test_inconsistent_declaration_fails_closed(tmp_path, monkeypatch, relative):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    file = tmp_path / relative
    file.write_text(file.read_text().replace('c0ff4eaef318cd59afcc61472ebe206362fc0211c4b589ed0d53946a569bd177', 'f' * 64))
    with pytest.raises(ValueError, match='provenance declaration'):
        identities.resolve('reasoning_gym')


def test_source_change_during_capture_is_rejected(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    capture = identities.capture_implementation
    def change(kind, root, components, **kwargs):
        if kind == 'scorer':
            file = root / 'data/datasets/reasoning_gym.py'
            file.write_bytes(file.read_bytes() + b'\n# concurrent change\n')
        return capture(kind, root, components, **kwargs)
    monkeypatch.setattr(identities, 'capture_implementation', change)
    with pytest.raises(ValueError, match='changed during capture'):
        identities.resolve('reasoning_gym')
