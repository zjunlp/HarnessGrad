"""Private measurement commitments affect comparisons without exporting gold hashes."""
import copy
import importlib.util
import json
from pathlib import Path

from harnessgrad import evidence_export, evidence_stats, verification_context
from harnessgrad.receipt_verification import canonical, digest, measurement_problems

spec = importlib.util.spec_from_file_location('context_export_fixtures',
                                             Path(__file__).with_name('test_evidence_export.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def create_runs(tmp_path):
    root = tmp_path / 'runs'
    _, left = fixtures.save_run(root, 'base')
    _, right = fixtures.save_run(root, 'sub', 1, tree='b')
    return root, left, right


def projections(pack):
    return [unit['projection']['curve'][0] for unit in pack['runs']]


def reseal(pack):
    for unit in pack['runs']:
        unit['projection_sha256'] = digest(canonical(unit['projection']))
    pack['content_sha256'] = digest(canonical({k: v for k, v in pack.items() if k != 'content_sha256'}))


def test_export_uses_only_opaque_equality_groups(tmp_path):
    root, left, right = create_runs(tmp_path)
    pack = evidence_export.build_export(fixtures.author(), root)
    assert pack['comparisons'][0]['comparable'] is True
    a, b = projections(pack)
    assert a['verification_context'] == b['verification_context'] == {
        'status': 'recorded', 'equivalence_group': 'verification-1'}
    raw = json.dumps(pack)
    assert fixtures.PRIVATE_TASK not in raw
    for value in (left['verification_context']['sha256'], *left['verification_context']['tasks'].values()):
        assert value not in raw
    assert evidence_export.check_export(pack) == []


def test_changed_private_inputs_are_incompatible_and_forged_comparison_rejected(tmp_path):
    root, _, right = create_runs(tmp_path)
    value = verification_context.create({task: 'e' * 64 for task in right['per_task']})
    fixtures.change(root, 'sub', 'curve.jsonl', lambda p: p.update(verification_context=value))
    pack = evidence_export.build_export(fixtures.author(), root)
    comparison = pack['comparisons'][0]
    assert not comparison['comparable']
    assert 'verification_context' in comparison['changed_conditions']
    assert projections(pack)[0]['verification_context'] != projections(pack)[1]['verification_context']
    assert value['sha256'] not in json.dumps(pack) and 'e' * 64 not in json.dumps(pack)
    comparison['comparable'] = True
    reseal(pack)
    assert evidence_export.check_export(pack) == ['export_verification_context_mismatch']


def test_legacy_missing_context_stays_readable_but_not_proven_comparable(tmp_path):
    root, left, _ = create_runs(tmp_path)
    legacy = copy.deepcopy(left)
    legacy.pop('verification_context')
    assert measurement_problems(legacy) == []
    for name in ('base', 'sub'):
        fixtures.change(root, name, 'curve.jsonl', lambda p: p.pop('verification_context'))
    pack = evidence_export.build_export(fixtures.author(), root)
    assert 'verification_context' in pack['comparisons'][0]['missing_conditions']
    assert not pack['comparisons'][0]['comparable']
    assert all(p['verification_context'] == {'status': 'unrecorded'} for p in projections(pack))
    assert evidence_export.check_export(pack) == []


def test_invalid_context_is_not_a_recorded_equality_claim():
    assert evidence_stats.public_verification_context({'verification_context': {'sha256': 'fake'}}) == {'status': 'invalid'}


def test_console_explain_allowlists_exclude_private_commitments(tmp_path):
    root, left, _ = create_runs(tmp_path)
    module_spec = importlib.util.spec_from_file_location('private_context_console',
        Path(__file__).resolve().parents[2] / 'console/server.py')
    console = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(console)
    inputs = console.build_explain_inputs(root / 'base', 'base', 0)
    raw = json.dumps(inputs)
    assert 'verification_context' not in raw
    assert left['verification_context']['sha256'] not in raw
    assert all(value not in raw for value in left['verification_context']['tasks'].values())
