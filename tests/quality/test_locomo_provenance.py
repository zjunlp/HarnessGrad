"""Synthetic LoCoMo provenance controls without conversation data or a model."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from harnessgrad.provenance import measurement_components, scoring_identity

ROOT = Path(__file__).resolve().parents[2]
PROFILE = 'data/datasets/locomo/measurement_profile.py'


def copied_profile(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('_locomo_profile_test', ROOT / PROFILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = set(module.LOADER.values()) | set(module.SCORER.values()) | {
        module.SOURCE, PROFILE, 'data/datasets/locomo/scoring_profile.py'}
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    monkeypatch.setattr(measurement_components, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    return module


def test_historical_identity_validates_without_claiming_new_equivalence():
    old = json.loads((ROOT / 'tests/fixtures/locomo_scoring_identity/before.json').read_text())
    assert scoring_identity.validate(old) == old
    current = scoring_identity.resolve('locomo')
    components = measurement_components.resolve('locomo')
    assert old['profile']['metric'] == current['profile']['metric']
    assert scoring_identity.problems(old, current)
    assert measurement_components.bundle_problems(None, components)
    measurement_components.require_scoring_alignment(current, components)


def test_loader_scorer_and_data_declarations_change_independently(tmp_path, monkeypatch):
    module = copied_profile(tmp_path, monkeypatch)
    before = measurement_components.resolve('locomo')
    before_score = scoring_identity.resolve('locomo')
    assert before['data_source']['details']['declared_artifact_sha256'] == '79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4'
    loader = tmp_path / module.LOADER['inputs']
    loader.write_bytes(loader.read_bytes() + b'\n# synthetic loader change\n')
    after_loader = measurement_components.resolve('locomo')
    assert after_loader['loader'] != before['loader']
    assert after_loader['scorer'] == before['scorer'] and after_loader['data_source'] == before['data_source']
    assert scoring_identity.resolve('locomo') == before_score
    scorer = tmp_path / module.SCORER['scoring']
    scorer.write_bytes(scorer.read_bytes() + b'\n# synthetic scorer change\n')
    after_scorer = measurement_components.resolve('locomo')
    score = scoring_identity.resolve('locomo')
    assert after_scorer['scorer'] != after_loader['scorer'] and after_scorer['loader'] == after_loader['loader']
    assert scoring_identity.problems(before_score, score)
    old_digest = before['data_source']['details']['declared_artifact_sha256']
    new_digest = 'f' * 64
    for relative in (module.ADAPTER, module.SOURCE):
        path = tmp_path / relative
        text = path.read_text()
        assert text.count(old_digest) == 1
        path.write_text(text.replace(old_digest, new_digest))
    after_data = measurement_components.resolve('locomo')
    assert after_data['data_source'] != after_scorer['data_source']
    assert after_data['scorer'] == after_scorer['scorer']
    assert scoring_identity.resolve('locomo') == score


def test_declared_scorer_provenance_is_not_data_or_loader(tmp_path, monkeypatch):
    module = copied_profile(tmp_path, monkeypatch)
    before = measurement_components.resolve('locomo')
    path = tmp_path / module.SOURCE
    spec = json.loads(path.read_text())
    spec['harbor']['files']['metric.py']['sha256'] = 'a' * 64
    path.write_text(json.dumps(spec))
    after = measurement_components.resolve('locomo')
    assert after['data_source'] == before['data_source'] and after['loader'] == before['loader']
    assert after['scorer'] != before['scorer']


@pytest.mark.parametrize('damage', ['source_hash', 'upstream_scorer_hash', 'duplicate_key'])
def test_invalid_source_and_scorer_declarations_are_refused(tmp_path, monkeypatch, damage):
    module = copied_profile(tmp_path, monkeypatch)
    path = tmp_path / module.SOURCE
    if damage == 'duplicate_key':
        path.write_text('{"data": {}, "data": {}}')
    else:
        value = json.loads(path.read_text())
        if damage == 'source_hash':
            value['data']['sha256'] = 'f' * 64
        else:
            value['harbor']['files']['metric.py']['sha256'] = 'invalid'
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='invalid LoCoMo provenance declaration'):
        measurement_components.resolve('locomo')


@pytest.mark.parametrize('which', ['adapter', 'source_manifest'])
def test_declaration_changed_during_capture_is_refused(tmp_path, monkeypatch, which):
    module = copied_profile(tmp_path, monkeypatch)
    path = tmp_path / PROFILE
    spec = importlib.util.spec_from_file_location('_locomo_profile_copy', path)
    copied = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copied)
    original = measurement_components.capture_implementation
    target = tmp_path / (module.ADAPTER if which == 'adapter' else module.SOURCE)
    def change_before_capture(kind, root, files, **kwargs):
        if kind == 'loader' and (which == 'adapter' or 'source_manifest' in files):
            target.write_bytes(target.read_bytes() + b'\n')
        return original(kind, root, files, **kwargs)
    monkeypatch.setattr(measurement_components, 'capture_implementation', change_before_capture)
    with pytest.raises(ValueError, match='declaration changed during capture'):
        copied.profile()
