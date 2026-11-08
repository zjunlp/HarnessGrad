"""External actor provenance exports only normalized digests and completeness."""
import copy
import json

from harnessgrad import evidence_stats as stats
from test_evidence_runtime_integration import state


def test_external_runtime_projection_never_exports_raw_binding_contents():
    point = {'identity': {'harness_name': 'codex', 'harness_sha': 'source'}, 'env': {'harness_runtime': [
        {'codex': {'kind': 'codex', 'complete': True, 'cacheable': True,
                   'installation_sha256': 'a' * 64, 'argv': ['/private/bin/codex', 'secret-input'],
                   'inventory': {'/private/file': 'secret-data'}}}]}}
    projected = stats.actor_runtime(point)
    assert projected['status'] == 'recorded'
    assert projected['external'][0]['kind'] == 'codex'
    assert 'private' not in json.dumps(projected) and 'secret' not in json.dumps(projected)
    assert set(projected['external'][0]) == {'kind', 'sha256', 'complete', 'cacheable'}
    incomplete = copy.deepcopy(point)
    incomplete['env']['harness_runtime'][0]['codex']['cacheable'] = False
    assert stats.actor_runtime(incomplete)['status'] == 'incomplete'


def test_old_named_external_records_remain_readable_but_unknown():
    assert stats.actor_runtime({'identity': {'harness_name': 'codex'}})['status'] == 'incomplete'
    assert stats.actor_runtime({'identity': {'harness_name': 'loop'}}) == {'status': 'unrecorded'}


def test_external_change_and_missing_evidence_block_comparison(monkeypatch):
    # Existing authored complete-condition fixture; no actual dataset/runtime.
    monkeypatch.setattr(stats, 'complete', lambda point: True)
    run, point = state()
    point['env']['harness_runtime'] = [{'codex': {'complete': True, 'cacheable': True,
                                               'installation_sha256': 'a' * 64}}]
    other = copy.deepcopy(point)
    assert stats.compare_conditions(run, point, run, other)['comparable']
    other['env']['harness_runtime'][0]['codex']['installation_sha256'] = 'b' * 64
    comparison = stats.compare_conditions(run, point, run, other)
    assert not comparison['comparable'] and 'external_actor_runtime' in comparison['changed_conditions']
    other['env'].pop('harness_runtime')
    comparison = stats.compare_conditions(run, point, run, other)
    assert not comparison['comparable'] and 'external_actor_runtime' in comparison['missing_conditions']


def test_actual_export_preserves_safe_external_identity_and_self_verifies(tmp_path):
    from harnessgrad.evidence_export import build_export, check_export
    from test_evidence_export import save_run, author
    for name, number, tree in [('base', 0, 'a'), ('sub', 1, 'b')]:
        save_run(tmp_path, name, number, tree=tree)
        path = tmp_path / name / 'curve.jsonl'
        point = json.loads(path.read_text())
        point['env']['harness_runtime'] = [{'dsh': {'kind': 'dsh', 'complete': True,
            'cacheable': True, 'installation_sha256': 'a' * 64,
            'argv': ['/operator/private/node', '/operator/private/cli.js'],
            'inventory': {'/operator/private/config': 'operator-secret'}}}]
        path.write_text(json.dumps(point) + '\n')
    bundle = build_export(author(), tmp_path)
    assert not check_export(bundle)
    serialized = json.dumps(bundle)
    assert '/operator/private' not in serialized and 'operator-secret' not in serialized
    for run in bundle['runs']:
        runtime = run['projection']['curve'][0]['harness_runtime']
        assert runtime['status'] == 'recorded'
        assert runtime['external'][0]['kind'] == 'dsh'
        assert runtime['external'][0]['complete'] and runtime['external'][0]['cacheable']


def test_explicit_managed_identity_takes_precedence_over_external_display_name():
    run, point = state()
    point['identity']['harness_name'] = 'codex'
    projected = stats.actor_runtime(point)
    assert projected['status'] == 'recorded'
    assert 'external' not in projected
