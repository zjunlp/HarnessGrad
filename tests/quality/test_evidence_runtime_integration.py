"""Synthetic runtime provenance and explicit mock receipt controls."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from harnessgrad import dataset_runtime_identity as dataset, evidence_stats, evidence_export, scoring_identity
from harnessgrad.python_artifacts import canonical
from harnessgrad.receipt_verification import ReceiptStore
from harnessgrad.store.schema import check_run_meta


def provider(ch):
    root = '/private/provider/' + ch
    return dataset.create(provider={'dataset': 'synthetic', 'runtime_id': ch * 64,
        'path': root + '/venv', 'python': root + '/venv/bin/python',
        'manifest_path': root + '/artifact.json', 'descriptor_sha256': 'd' * 64,
        'requirements_sha256': 'e' * 64})


def actor(source, artifact):
    venv = '/private/runtime/instances/build-x/venv'
    value = {'version': 3, 'kind': 'python', 'source_sha256': source * 64,
             'declaration_sha256': 'd' * 64, 'artifact': {'runtime_id': artifact * 64,
             'dependency_key': 'e' * 64, 'path': venv, 'python': venv + '/bin/python',
             'manifest_path': '/private/runtime/instances/build-x/artifact.json', 'runtime_paths': [venv]}}
    return {**value, 'sha256': hashlib.sha256(canonical(value)).hexdigest()}


def state():
    from harnessgrad import verification_context
    run = {'run_id': 'one', 'meta': {'dataset': 'synthetic', 'platform': {'content_sha': 'p'},
        'config': {'agent': {}, 'environment': {}, 'judge': {}, 'sandbox': {}}}}
    point = {'run_id': 'one', 'round': 0, 'identity': {'agent_model': 'm', 'agent_backend': 'b',
        'harness_name': 'h', 'harness_sha': 'tree', 'framework_version': 'v', 'trials': 1, 'jobs': 1},
        'score': 0.5, 'side': 'eval', 'score_kind': 'exam', 'scoring_identity': {'sha256': 's'},
        'env': {}, 'sampling': {'policy': 'all'}, 'per_task': {'private-task': .5},
        'verification_context': verification_context.create({'private-task': '9' * 64}),
        'evaluation_protocols': {}, 'scorer_isolation_class': 'separate-pristine',
        'per_task_scorer_isolation': {'private-task': 'separate-pristine'}, 'measured_by_platform': True,
        'dataset_runtime_identity': provider('a'), 'harness_runtime_identity': actor('a', 'a')}
    return run, point


def test_fixed_environment_comparison_and_private_projection(monkeypatch):
    monkeypatch.setattr(evidence_stats, 'complete', lambda p: True)
    run, left = state()
    right = copy.deepcopy(left)
    right['dataset_runtime_identity'] = provider('b')
    result = evidence_stats.compare_conditions(run, left, run, right)
    assert not result['comparable'] and 'dataset_runtime_identity' in result['changed_conditions']
    right['dataset_runtime_identity'] = None
    assert 'dataset_runtime_identity' in evidence_stats.compare_conditions(run, left, run, right)['missing_conditions']
    right['dataset_runtime_identity'] = provider('a')
    right['harness_runtime_identity'] = actor('b', 'b')
    assert evidence_stats.compare_conditions(run, left, run, right)['comparable']
    projection = evidence_export._point(left)
    encoded = json.dumps(projection)
    assert '/private/' not in encoded
    assert projection['dataset_runtime']['sha256'] == left['dataset_runtime_identity']['sha256']
    assert projection['harness_runtime']['source_sha256'] == 'a' * 64
    assert evidence_stats.dataset_runtime({'dataset_runtime_identity': {'sha256': 'fake'}})['status'] == 'invalid'


def test_same_tree_variance_rejects_different_actor_dependencies(monkeypatch):
    monkeypatch.setattr(evidence_stats, 'complete', lambda p: True)
    run, left = state()
    other_run, right = copy.deepcopy(run), copy.deepcopy(left)
    other_run['run_id'] = 'two'
    right['harness_runtime_identity'] = actor('a', 'b')
    result = evidence_stats.run_variation([(run, left), (other_run, right)])
    assert result['reason'] == 'different_or_invalid_harness_runtime_dependencies'
    right['harness_runtime_identity'] = actor('b', 'a')
    assert evidence_stats.run_variation([(run, left), (other_run, right)])['status'] == 'unavailable'
    assert evidence_stats.compare_conditions(run, left, other_run, right)['comparable']
    right['harness_runtime_identity'] = actor('a', 'a')
    assert evidence_stats.run_variation([(run, left), (other_run, right)])['status'] == 'available'


def test_export_verifier_rejects_variance_with_different_source_fingerprints(tmp_path):
    spec = importlib.util.spec_from_file_location('evidence_export_fixtures',
        Path(__file__).with_name('test_evidence_export.py'))
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    root = tmp_path / 'runs'
    fixtures.save_run(root, 'base')
    fixtures.save_run(root, 'sub', 1, tree='a')
    for name in ('base', 'sub'):
        fixtures.change(root, name, 'curve.jsonl', lambda p: p.update(harness_runtime_identity=actor('a', 'a')))
    document = fixtures.author()
    document['variation_groups'] = [{'id': 'repeat', 'states': [
        {'run_id': 'base', 'round': 0}, {'run_id': 'sub', 'round': 1}]}]
    bundle = evidence_export.build_export(document, root)
    assert bundle['variation_groups'][0]['status'] == 'available'
    unit = next(item for item in bundle['runs'] if item['run_id'] == 'sub')
    unit['projection']['curve'][0]['harness_runtime']['source_sha256'] = 'b' * 64
    unit['projection_sha256'] = evidence_export.digest(evidence_export.canonical(unit['projection']))
    bundle['content_sha256'] = evidence_export.digest(evidence_export.canonical(
        {key: value for key, value in bundle.items() if key != 'content_sha256'}))
    assert evidence_export.check_export(bundle) == ['export_run_variation_mismatch']


@pytest.mark.parametrize('backend,model,present,accepted', [('mock', None, True, True),
    ('openai', None, True, False), ('mock', None, False, True), (None, None, True, False)])
def test_mock_receipt_requires_explicit_backend_and_null_model(tmp_path, backend, model, present, accepted):
    run, point = state()
    point.update(n_scored=1, cost={}, sampling={'policy': 'all', 'task_ids': ['private-task']})
    point['timestamp_utc'] = '2026-11-06T00:00:00Z'
    point['scoring_identity'] = scoring_identity.create({'metric': 'synthetic.mean', 'scorer_sha256': 'f' * 64})
    meta = {'run_id': 'one', 'agent_backend': backend, 'harness': 'h', 'dataset': 'synthetic',
            'workspace': '/private/work', 'platform': {'content_sha': 'p'}}
    if present:
        meta['agent_model'] = model
    root = tmp_path / 'runs'; directory = root / 'one'; directory.mkdir(parents=True)
    (directory / 'run_meta.json').write_text(json.dumps(meta))
    (directory / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    store = ReceiptStore(root)
    try:
        measured = store.measured(store.load_run('one'))
        assert bool(measured) == accepted
        assert (not any('`agent_model`' in p for p in check_run_meta(meta)[0])) == accepted if backend in ('mock', 'openai') else True
    finally:
        store.close()
