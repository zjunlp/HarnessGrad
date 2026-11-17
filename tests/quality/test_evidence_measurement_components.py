"""Component comparison survives privacy-safe evidence export."""
import importlib.util
import json
from pathlib import Path

import pytest

from harnessgrad.evidence_reports import evidence_export
from harnessgrad.provenance import measurement_components as identities
from harnessgrad.evidence_reports.receipt_verification import canonical, digest

spec = importlib.util.spec_from_file_location('component_export_fixtures',
    Path(__file__).with_name('test_evidence_export.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def bundle(loader='a'):
    return {'data_source': identities.data_source('synthetic', origin='private-source-label', revision='v1'),
            'loader': identities.implementation('loader', {'provider': loader * 64}),
            'scorer': identities.implementation('scorer', {'verify': 'b' * 64})}


def test_export_groups_component_equality_and_rejects_forged_comparability(tmp_path):
    root = tmp_path / 'runs'
    fixtures.save_run(root, 'base')
    fixtures.save_run(root, 'sub', 1, tree='b')
    fixtures.change(root, 'base', 'curve.jsonl', lambda p: p.update(measurement_components=bundle()))
    fixtures.change(root, 'sub', 'curve.jsonl', lambda p: p.update(measurement_components=bundle('c')))
    pack = evidence_export.build_export(fixtures.author(), root)
    comparison = pack['comparisons'][0]
    assert not comparison['comparable']
    assert 'measurement_components.loader' in comparison['changed_conditions']
    a, b = [unit['projection']['curve'][0]['measurement_components'] for unit in pack['runs']]
    assert a['groups']['data_source'] == b['groups']['data_source']
    assert a['groups']['loader'] != b['groups']['loader']
    assert 'private-source-label' not in json.dumps(pack) and 'a' * 64 not in json.dumps(pack)
    assert evidence_export.check_export(pack) == []
    comparison['comparable'] = True
    pack['content_sha256'] = digest(canonical({k: v for k, v in pack.items() if k != 'content_sha256'}))
    assert evidence_export.check_export(pack) == ['export_measurement_components_mismatch']


def test_legacy_and_invalid_components_do_not_establish_equality(tmp_path):
    root = tmp_path / 'runs'
    fixtures.save_run(root, 'base')
    fixtures.save_run(root, 'sub', 1, tree='b')
    fixtures.change(root, 'base', 'curve.jsonl', lambda p: p.update(measurement_components=bundle()))
    pack = evidence_export.build_export(fixtures.author(), root)
    assert not pack['comparisons'][0]['comparable']
    assert 'measurement_components' in pack['comparisons'][0]['missing_conditions']
    assert evidence_export.check_export(pack) == []


@pytest.mark.parametrize('dataset', ['agentdojo', 'alfworld', 'jericho', 'scienceworld'])
def test_dataset_provenance_is_grouped_without_exposing_raw_identity(tmp_path, dataset):
    from harnessgrad.provenance import scoring_identity

    original = identities.resolve(dataset)
    scoring = scoring_identity.resolve(dataset)
    identities.require_scoring_alignment(scoring, original)
    changed = dict(original)
    loader = original['loader']['details']
    changed['loader'] = identities.implementation('loader', {**loader['components'], 'authored_probe': 'c' * 64},
                                                   protocol=loader['protocol'])
    root = tmp_path / 'runs'
    for name, number, bundle in (('base', 0, original), ('sub', 1, changed)):
        fixtures.save_run(root, name, number, tree='a' if name == 'base' else 'b')
        fixtures.change(root, name, 'run_meta.json', lambda meta: meta.update(dataset=dataset))
        def update(point):
            point['sampling']['dataset'] = dataset
            point.update(measurement_components=bundle, scoring_identity=scoring)
        fixtures.change(root, name, 'curve.jsonl', update)
    pack = evidence_export.build_export(fixtures.author(), root)
    comparison = pack['comparisons'][0]
    assert not comparison['comparable']
    assert 'measurement_components.loader' in comparison['changed_conditions']
    a, b = [unit['projection']['curve'][0]['measurement_components'] for unit in pack['runs']]
    assert a['groups']['data_source'] == b['groups']['data_source']
    assert a['groups']['loader'] != b['groups']['loader']
    encoded = canonical(pack)
    assert original['data_source']['details']['origin'].encode() not in encoded
    declared = original['data_source']['details']['declared_artifact_sha256']
    if declared is not None:
        assert declared.encode() not in encoded
    assert original['loader']['sha256'].encode() not in encoded
    assert not evidence_export.check_export(pack)
