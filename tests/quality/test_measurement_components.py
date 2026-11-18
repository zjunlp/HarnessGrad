"""Offline controls for independent measurement provenance components."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from harnessgrad.provenance import measurement_components as identities

ROOT = Path(__file__).resolve().parents[2]


def test_source_is_an_explicit_declaration_and_checked_independently():
    first = identities.data_source('synthetic', origin='https://example.test/data',
                                   revision='v1', declared_artifact_sha256='a' * 64)
    changed = identities.data_source('synthetic', origin='https://example.test/data',
                                     revision='v2', declared_artifact_sha256='a' * 64)
    assert identities.validate(first, kind='data_source') == first
    assert first['details']['declared_artifact_sha256'] == 'a' * 64
    assert 'verified' not in first['details']
    assert identities.problems(first, changed, kind='data_source') == ['data_source identity changed']


def test_implementation_identity_is_role_specific_and_independent_of_paths(tmp_path):
    (tmp_path / 'old.py').write_bytes(b'implementation')
    (tmp_path / 'new.py').write_bytes(b'implementation')
    first = identities.capture_implementation('loader', tmp_path, {'provider': 'old.py'})
    relocated = identities.capture_implementation('loader', tmp_path, {'provider': 'new.py'})
    scorer = identities.capture_implementation('scorer', tmp_path, {'provider': 'new.py'})
    assert first == relocated
    assert identities.problems(first, scorer, kind='loader') == ['invalid loader identity on current']
    (tmp_path / 'new.py').write_bytes(b'changed implementation')
    changed = identities.capture_implementation('loader', tmp_path, {'provider': 'new.py'})
    assert identities.problems(first, changed, kind='loader') == ['loader identity changed']


def test_unknown_tampered_or_mislabelled_components_never_compare_equal():
    identity = identities.implementation('scorer', {'verify': 'b' * 64})
    assert identities.problems(None, None, kind='scorer')
    assert identities.problems(identity, None, kind='scorer')
    tampered = deepcopy(identity)
    tampered['details']['components']['verify'] = 'c' * 64
    with pytest.raises(ValueError, match='digest mismatch'):
        identities.validate(tampered)
    assert identities.problems(identity, tampered, kind='scorer')
    with pytest.raises(ValueError):
        identities.validate({**identity, 'kind': []})


def test_scoring_and_component_captures_must_name_the_same_scorer():
    from harnessgrad.provenance import scoring_identity
    bundle = {'data_source': identities.data_source('synthetic', origin='source', revision='v1'),
              'loader': identities.implementation('loader', {'provider': 'a' * 64}),
              'scorer': identities.implementation('scorer', {'verify': 'b' * 64})}
    good = scoring_identity.create({'metric': 'synthetic', 'scorer_sha256': bundle['scorer']['sha256']})
    identities.require_scoring_alignment(good, bundle)
    bad = scoring_identity.create({'metric': 'synthetic', 'scorer_sha256': 'c' * 64})
    with pytest.raises(ValueError, match='does not match'):
        identities.require_scoring_alignment(bad, bundle)


@pytest.mark.parametrize('path', ['../outside', '/absolute', 'dir/../outside', 'dir\\file'])
def test_implementation_capture_rejects_untrusted_paths(tmp_path, path):
    with pytest.raises(ValueError):
        identities.capture_implementation('loader', tmp_path, {'provider': path})


def test_implementation_capture_rejects_symlinks_outside_root(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'outside')
    (root / 'link').symlink_to(outside)
    with pytest.raises(ValueError, match='escapes root'):
        identities.capture_implementation('loader', root, {'provider': 'link'})


def test_chessbench_profile_separates_declared_source_loader_and_scorer(tmp_path, monkeypatch):
    path = ROOT / 'data/datasets/chessbench_puzzles/measurement_profile.py'
    spec = importlib.util.spec_from_file_location('test_chess_measurement_profile', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = set(module.LOADER.values()) | set(module.SCORER.values())
    for relative in paths | {path.relative_to(ROOT).as_posix()}:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    before = identities.resolve('chessbench_puzzles')
    source = before['data_source']['details']
    assert source['origin'] == 'https://storage.googleapis.com/searchless_chess/data/puzzles.csv'
    assert source['revision'] == '90ae0e6b121673fc3079aaeffa047580bb600c0a'
    assert source['declared_artifact_sha256'] == '099d54e681a07b9e82b4462aaf5436a6e833edc6f56b92ea7c2d2413f7efbe48'
    assert before['scorer']['details']['protocol'] == 'ChessBench strict legal puzzle replay v1'
    provider = tmp_path / module.LOADER['provider']
    provider.write_bytes(provider.read_bytes() + b'\n# synthetic provider change\n')
    after = identities.resolve('chessbench_puzzles')
    assert before['loader'] != after['loader']
    assert before['data_source'] == after['data_source']
    assert before['scorer'] == after['scorer']
    scorer = tmp_path / module.SCORER['qa_verifier']
    scorer.write_bytes(scorer.read_bytes() + b'\n# synthetic scorer change\n')
    final = identities.resolve('chessbench_puzzles')
    assert after['scorer'] != final['scorer']
    assert after['loader'] == final['loader']


def test_profile_resolution_rejects_missing_or_wrong_components(tmp_path, monkeypatch):
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path)
    assert identities.resolve('absent') is None
    with pytest.raises(ValueError, match='invalid dataset name'):
        identities.resolve('../escape')
    source = identities.data_source('other', origin='source', revision='v1')
    loader = identities.implementation('loader', {'provider': 'a' * 64})
    scorer = identities.implementation('scorer', {'verify': 'b' * 64})
    with pytest.raises(ValueError, match='dataset mismatch'):
        identities.validate_bundle({'data_source': source, 'loader': loader, 'scorer': scorer}, dataset='expected')
    with pytest.raises(ValueError, match='requires'):
        identities.validate_bundle({'data_source': source}, dataset='other')


def test_record_and_comparison_reject_missing_or_changed_components():
    from harnessgrad.experiment import policies
    from harnessgrad.experiment.records import _curve_point
    from harnessgrad.store.schema import check_point
    from harnessgrad.orchestration.run_metadata import IdentityRecords, identity_fields

    bundle = {'data_source': identities.data_source('synthetic', origin='source', revision='v1'),
              'loader': identities.implementation('loader', {'provider': 'a' * 64}),
              'scorer': identities.implementation('scorer', {'verify': 'b' * 64})}
    def point(components):
        return _curve_point(run_id='test', round_index=0, identity={}, sampling={'task_ids': ['t']},
                            scores=[.5], task_ids=['t'], cost={}, cumulative={},
                            measurement_components=components)
    recorded = point(bundle)
    assert recorded['measurement_components'] == bundle
    assert identity_fields(IdentityRecords(None, None, None, None, bundle))['measurement_components'] == bundle
    assert 'measurement_components' not in point(None)
    assert any('measurement_components missing' in p for p in policies.reference_problems(recorded, point(None)))
    changed = deepcopy(bundle)
    changed['loader'] = identities.implementation('loader', {'provider': 'c' * 64})
    assert 'loader identity changed' in policies.reference_problems(recorded, point(changed))
    bad = deepcopy(recorded)
    bad['measurement_components']['scorer']['sha256'] = '0' * 64
    assert any('invalid measurement_components' in p for p in check_point(bad)[0])


def test_resume_rejects_missing_component_evidence_in_a_recorded_point(tmp_path):
    from harnessgrad.orchestration.resume import ResumeIdentities, _admit_initial_points
    from harnessgrad.provenance import verification_context

    bundle = {'data_source': identities.data_source('synthetic', origin='source', revision='v1'),
              'loader': identities.implementation('loader', {'provider': 'a' * 64}),
              'scorer': identities.implementation('scorer', {'verify': 'b' * 64})}
    context = verification_context.create({'t': 'c' * 64})
    current = ResumeIdentities(None, None, context, bundle)
    metadata = {'verification_context': context, 'measurement_components': bundle}
    point = {'sampling': {'task_ids': ['t']}, 'verification_context': context,
             'measurement_components': bundle}
    path = tmp_path / 'curve.jsonl'
    path.write_text(json.dumps(point) + '\n')
    assert _admit_initial_points(tmp_path, metadata, current) == [point]
    del point['measurement_components']
    path.write_text(json.dumps(point) + '\n')
    with pytest.raises(ValueError, match='measurement_components missing'):
        _admit_initial_points(tmp_path, metadata, current)
