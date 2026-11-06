"""Synthetic offline evidence: known scores, incomplete attempts and private data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from harnessgrad import evidence, scoring_identity
from harnessgrad.evidence_export import build_export, check_export, write_export
from harnessgrad.receipt_verification import ReceiptStore, canonical, digest, verify_receipts

ROOT = Path(__file__).resolve().parents[2]
SECRET = 'sk-' + 'hiddenCredential' * 3
PRIVATE_TASK = 'private task question and answer'


def author():
    return {'schema': evidence.SCHEMA, 'submission': {'run_id': 'sub', 'round': 1},
            'requests': [{'id': 'R1', 'concern': 'Compare the recorded candidate with baseline.',
                          'runs': ['base', 'sub'],
                          'result': {'run_id': 'sub', 'round': 1, 'metric': 'score'},
                          'vs_baseline': {'baseline_run': 'base', 'baseline_round': 0}}],
            'attempts': [{'run_id': 'base', 'purpose': 'baseline'}, {'run_id': 'sub', 'purpose': 'follow-up'}],
            'selection': {'reported': {'run_id': 'sub', 'round': 1}, 'rule': 'predeclared final round'},
            'budget': {'rounds': 1, 'trials': 3, 'source': 'author protocol'}}


def save_run(root, name, number=0, *, tree='a', rows=None):
    rows = rows or [[0, 1, 0], [1, 1, 1]]
    tasks = {PRIVATE_TASK + str(i): sum(row) / len(row) for i, row in enumerate(rows)}
    meta = {'run_id': name, 'harness': 'loop', 'dataset': 'synthetic', 'workspace': '/private/workspace',
            'agent_model': 'solver', 'agent_backend': 'openai', 'platform': {'content_sha': 'p' * 64},
            'config': {'agent': {'model': 'solver', 'temperature': 0, 'api_key': SECRET,
                                 'base_url': 'https://user:password@private.example/v1?token=secret'},
                       'sandbox': {'memory_mb': 256}, 'environment': {}, 'judge': {},
                       'run': {'rounds': number, 'trials': len(rows[0]), 'jobs': 1}},
            'completion': {'status': 'complete', 'requested_rounds': number, 'completed_rounds': number},
            'private_note': PRIVATE_TASK}
    point = {'run_id': name, 'round': number,
             'identity': {'harness_name': 'loop', 'harness_sha': tree * 40,
                          'agent_model': 'solver', 'agent_backend': 'openai', 'method': 'toy',
                          'framework_version': '0.1.0', 'trials': len(rows[0]), 'jobs': 1},
             'score': sum(tasks.values()) / len(tasks), 'score_kind': 'exam', 'side': 'eval',
             'per_task': tasks, 'n_scored': len(tasks), 'env': {'kind': 'files'},
             'sampling': {'policy': 'all', 'task_ids': list(tasks), 'dataset': 'synthetic'},
             'cost': {'evaluation_trials': sum(len(row) for row in rows), 'harness_tokens': 100,
                      'method_generation_tokens': 15, 'wall_clock_s': 1},
             'timestamp_utc': '2026-11-06T00:00:00Z', 'measured_by_platform': True,
             'n_trials': len(rows[0]), 'per_task_trials': dict(zip(tasks, rows)),
             'score_ci95': [float('nan'), float('nan')],
             'scoring_identity': scoring_identity.create({'metric': 'synthetic.mean', 'scorer_sha256': 'f' * 64}),
             'evaluation_protocols': {}, 'scorer_isolation_class': 'separate-pristine',
             'per_task_scorer_isolation': {task: 'separate-pristine' for task in tasks},
             'invalid': {}, 'n_invalid': 0, 'harness_failed': {}, 'timeouts': {},
             'private_note': PRIVATE_TASK}
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'run_meta.json').write_text(json.dumps(meta))
    (directory / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    (directory / 'diffs').mkdir()
    (directory / 'diffs' / f'round-{number}.patch').write_text('+ ' + PRIVATE_TASK + '\n+ api_key=' + SECRET)
    (directory / 'trace.jsonl').write_text(PRIVATE_TASK)
    return meta, point


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'runs'
    save_run(root, 'base')
    save_run(root, 'sub', 1, tree='b')
    return root


def change(root, name, filename, mutate):
    path = root / name / filename
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data) + '\n')


def unit(bundle, name):
    return next(r for r in bundle['runs'] if r['run_id'] == name)


def test_portable_export_matches_sources_and_preserves_privacy(source, tmp_path):
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    pack = build_export(author(), source)
    assert not check_export(pack)
    sub = unit(pack, 'sub')
    assert sub['source_sha256']['curve.jsonl'] == digest((source / 'sub/curve.jsonl').read_bytes())
    assert pack['manifest']['requests'][0]['result']['value'] == pytest.approx(2 / 3)
    assert sub['projection']['curve'][0]['trial_variation']['sample_variance'] == pytest.approx(1 / 12)
    assert sub['projection']['patches'][0]['status'] == 'withheld_for_privacy'
    encoded = canonical(pack)
    for secret in (SECRET, PRIVATE_TASK, '/private/workspace', 'private.example', 'api_key'):
        assert secret.encode() not in encoded
    output = tmp_path / 'evidence.json'
    write_export(pack, output)
    assert output.stat().st_mode & 0o777 == 0o600
    assert before == {p: p.read_bytes() for p in before}
    with pytest.raises(FileExistsError):
        write_export(pack, output)
    assert output.read_bytes() == canonical(pack) + b'\n'


def test_discovers_recording_only_and_failed_or_stopped_attempts(source):
    (source / 'no-curve' / 'recordings').mkdir(parents=True)
    for name, status in [('crashed', 'failed'), ('cancelled', 'stopped')]:
        save_run(source, name)
        change(source, name, 'run_meta.json', lambda m: m.update(completion={'status': status, 'reason': SECRET}))
    pack = build_export(author(), source)
    attempts = {a['run_id']: a for a in pack['manifest']['attempts']}
    assert attempts['no-curve']['status'] == 'unmeasured'
    assert attempts['crashed']['status'] == 'failed'
    assert attempts['cancelled']['status'] == 'stopped'
    assert attempts['crashed']['rounds'] == [0]
    assert pack['discovery']['unexplained_attempts'] == ['cancelled', 'crashed', 'no-curve']
    assert pack['discovery']['global_completeness'] == 'not_established'
    assert unit(pack, 'no-curve')['budget']['totals']['harness_tokens']['recorded_sum'] is None


@pytest.mark.parametrize('status', ['failed', 'stopped', 'invalid', 'unmeasured'])
def test_author_exclusions_cannot_be_silently_promoted_to_results(source, status):
    doc = author()
    doc['attempts'][1].update(status=status, reason='operator invalidated the measurement')
    with pytest.raises(evidence.EvidenceError, match='manifest_inconsistent'):
        build_export(doc, source)


def test_unmeasured_concern_is_preserved(source):
    doc = author()
    doc['requests'].append({'id': 'R2', 'concern': 'Transfer', 'runs': [],
                            'status': 'unmeasured', 'reason': 'not run'})
    assert build_export(doc, source)['manifest']['requests'][1]['status'] == 'unmeasured'


def test_missing_referenced_attempt_and_missing_baseline_round_refuse(source):
    doc = author()
    doc['requests'][0]['vs_baseline'].pop('baseline_round')
    with pytest.raises(evidence.EvidenceError, match='explicit_round_required'):
        build_export(doc, source)
    doc = author()
    doc['attempts'].append({'run_id': 'ghost', 'purpose': 'attempt'})
    with pytest.raises(evidence.EvidenceError, match='directory_missing'):
        build_export(doc, source)


@pytest.mark.parametrize('value', [0.8, True, float('nan'), float('inf'), '0.5'])
def test_cannot_launder_a_fabricated_score(source, value):
    doc = author()
    doc['requests'][0]['result']['value'] = value
    with pytest.raises(evidence.EvidenceError, match='claimed_score_mismatch'):
        build_export(doc, source)


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(measured_by_platform=False),
    lambda p: p.update(startup_failure={'detail': SECRET}),
    lambda p: p.update(tampered={'files': [PRIVATE_TASK]}),
    lambda p: p.update(score=float('inf')),
    lambda p: p.update(n_scored=True),
    lambda p: p['identity'].update(harness_sha=['wrong']),
])
def test_claimed_or_unusable_points_never_become_results(source, mutate):
    change(source, 'sub', 'curve.jsonl', mutate)
    with pytest.raises(evidence.EvidenceError, match='missing_or_invalid_result'):
        build_export(author(), source)


def test_duplicate_round_or_duplicate_json_key_refuses(source):
    path = source / 'sub/curve.jsonl'
    original = path.read_text()
    path.write_text(original + original)
    with pytest.raises(evidence.EvidenceError):
        build_export(author(), source)
    path.write_text(original.replace('"run_id": "sub"', '"run_id": "sub", "run_id": "base"'))
    with pytest.raises(evidence.EvidenceError):
        build_export(author(), source)


def test_partial_measurement_keeps_coverage_without_imputation(source):
    def partial(p):
        p['sampling']['task_ids'].append('another private task')
        p.update(invalid={'another private task': 'private reason'}, n_invalid=1)
    change(source, 'sub', 'curve.jsonl', partial)
    point = unit(build_export(author(), source), 'sub')['projection']['curve'][0]
    assert point['score'] == pytest.approx(2 / 3)
    assert point['coverage']['invalid'] == 1
    assert point['trial_variation']['reason'] == 'incomplete_task_coverage'


def test_baseline_comparison_is_an_actual_diff_and_not_author_claim(source):
    doc = author()
    doc['requests'][0]['vs_baseline']['diff'] = {'changed': False, 'config': [SECRET]}
    pack = build_export(doc, source)
    comparison = pack['comparisons'][0]
    assert comparison['comparable']
    assert comparison['diff']['changed']
    assert comparison['diff']['harness_sha'] == {'before': 'a' * 40, 'after': 'b' * 40}
    assert comparison['diff']['config'] == [{'field': 'run.rounds', 'before': 0, 'after': 1,
                                            'before_recorded': True, 'after_recorded': True}]
    change(source, 'sub', 'curve.jsonl', lambda p: p['identity'].update(agent_model='different'))
    comparison = build_export(doc, source)['comparisons'][0]
    assert not comparison['comparable']
    assert 'identity.agent_model' in comparison['changed_conditions']


def test_costs_are_scoped_and_multi_candidate_spend_is_not_double_counted(source):
    change(source, 'sub', 'curve.jsonl', lambda p: p.update(steps_in_submission=3))
    totals = unit(build_export(author(), source), 'sub')['budget']['totals']
    assert totals['evaluation_trials']['recorded_sum'] == 6
    assert totals['env_generation_tokens']['recorded_sum'] is None
    assert totals['method_generation_tokens']['recorded_sum'] is None
    assert totals['method_generation_tokens']['reason'] == 'ambiguous_submission_cost'


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'hardlink', 'oversized'])
def test_unsafe_or_oversized_receipts_are_not_followed(source, tmp_path, kind):
    path = source / 'sub/curve.jsonl'
    content = path.read_bytes()
    path.unlink()
    outside = tmp_path / 'private.json'
    outside.write_bytes(content)
    if kind == 'symlink':
        path.symlink_to(outside)
    elif kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'hardlink':
        os.link(outside, path)
    else:
        path.write_bytes(content + b' ' * 9000)
    with pytest.raises(evidence.EvidenceError):
        build_export(author(), source, max_bytes=8000)


def test_symlink_root_and_traversal_are_refused(source, tmp_path):
    link = tmp_path / 'alias'
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        build_export(author(), link)
    doc = author()
    doc['submission']['run_id'] = '../private'
    with pytest.raises(evidence.EvidenceError):
        build_export(doc, source)


def test_missing_patch_is_explicit_and_never_faked(source):
    (source / 'sub/diffs/round-1.patch').unlink()
    patch = unit(build_export(author(), source), 'sub')['projection']['patches'][0]
    assert patch['status'] == 'missing' and patch['sha256'] is None


def test_receipt_mutation_during_export_refuses(source, monkeypatch):
    original = ReceiptStore.recheck
    def mutate(store):
        (source / 'sub/curve.jsonl').write_text('{}\n')
        original(store)
    monkeypatch.setattr(ReceiptStore, 'recheck', mutate)
    with pytest.raises(evidence.EvidenceError, match='receipt_changed'):
        build_export(author(), source)


def test_export_checksum_detects_tampering(source):
    pack = build_export(author(), source)
    pack['manifest']['requests'][0]['result']['value'] = 0.9
    assert check_export(pack) == ['export_digest_mismatch']


def test_receipt_verifier_checks_real_sources(source):
    manifest = build_export(author(), source)['manifest']
    assert verify_receipts(manifest, source)['valid']
    manifest['requests'][0]['result']['value'] = 0.8
    assert verify_receipts(manifest, source)['findings'][0]['code'] == 'value_mismatch'


def test_cli_exports_verifies_and_refuses_overwrite_without_leaking(source, tmp_path):
    document = tmp_path / 'author.json'
    document.write_text(json.dumps(author()))
    output = tmp_path / 'pack.json'
    argv = [sys.executable, str(ROOT / 'tools/evidence_export.py')]
    exported = subprocess.run(argv + ['--manifest', str(document), '--runs-root', str(source), '--out', str(output)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert exported.returncode == 0, exported.stderr
    verified = subprocess.run(argv + ['--verify', str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert verified.returncode == 0, verified.stderr
    refused = subprocess.run(argv + ['--manifest', str(document), '--runs-root', str(source), '--out', str(output)],
                             cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert refused.returncode == 2
    assert SECRET not in refused.stderr and str(tmp_path) not in refused.stderr


@pytest.mark.parametrize('mutate,reason', [
    (lambda p: p.update(n_trials=1), 'fewer_than_two_recorded_trials'),
    (lambda p: p.pop('per_task_trials'), 'missing_trial_records'),
    (lambda p: p['per_task_trials'].pop(next(iter(p['per_task_trials']))), 'missing_trial_records'),
    (lambda p: next(iter(p['per_task_trials'].values())).pop(), 'incomplete_or_nonfinite_trials'),
    (lambda p: next(iter(p['per_task_trials'].values())).__setitem__(0, float('nan')), 'incomplete_or_nonfinite_trials'),
    (lambda p: next(iter(p['per_task_trials'].values())).__setitem__(0, 1), 'trial_mean_disagrees_with_task_score'),
    (lambda p: p.update(reused_from=0), 'reused_or_cached_measurement'),
    (lambda p: p.update(shared_from={'run_id': 'base'}), 'reused_or_cached_measurement'),
    (lambda p: p.update(n_cached=1), 'reused_or_cached_measurement'),
    (lambda p: p['identity'].update(trials=2), 'conflicting_trial_counts'),
])
def test_variation_does_not_invent_observations(source, mutate, reason):
    change(source, 'sub', 'curve.jsonl', mutate)
    variation = unit(build_export(author(), source), 'sub')['projection']['curve'][0]['trial_variation']
    assert variation['status'] == 'unavailable'
    assert variation['sample_variance'] is None
    assert variation['reason'] == reason


def replica_document():
    doc = author()
    doc['variation_groups'] = [{'id': 'repeated-candidate', 'states': [
        {'run_id': 'sub', 'round': 1}, {'run_id': 'repeat', 'round': 1}]}]
    return doc


def test_between_run_variance_uses_distinct_explicit_comparable_states(source):
    save_run(source, 'repeat', 1, tree='b', rows=[[1, 1, 1], [1, 1, 1]])
    variation = build_export(replica_document(), source)['variation_groups'][0]
    assert variation['status'] == 'available'
    assert variation['n'] == 2
    assert variation['sample_variance'] == pytest.approx(1 / 18)
    assert variation['scope'] == 'between_runs_same_harness'
    assert variation['independence'] == 'distinct_run_ids_only'


@pytest.mark.parametrize('mutate', [
    lambda p: p['identity'].update(harness_sha='c' * 40),
    lambda p: p['identity'].update(agent_model='other'),
    lambda p: p.update(scoring_identity=scoring_identity.create({'metric': 'other'})),
    lambda p: p.pop('scoring_identity'),
    lambda p: p.update(shared_from={'run_id': 'sub'}),
    lambda p: p['sampling']['task_ids'].append('missing private task'),
])
def test_between_run_variation_refuses_unjustified_combinations(source, mutate):
    save_run(source, 'repeat', 1, tree='b')
    change(source, 'repeat', 'curve.jsonl', mutate)
    variation = build_export(replica_document(), source)['variation_groups'][0]
    assert variation['status'] == 'unavailable' and variation['sample_variance'] is None


def test_duplicate_run_and_single_run_are_not_replicates(source):
    doc = replica_document()
    doc['variation_groups'][0]['states'] = [{'run_id': 'sub', 'round': 1}] * 2
    assert build_export(doc, source)['variation_groups'][0]['reason'] == 'repeated_run_id'
    doc['variation_groups'][0]['states'].pop()
    assert build_export(doc, source)['variation_groups'][0]['reason'] == 'fewer_than_two_runs'


def test_platform_resume_seam_makes_comparability_unknown(source):
    change(source, 'sub', 'run_meta.json', lambda m: m.update(platform_content_sha_started='old'))
    comparison = build_export(author(), source)['comparisons'][0]
    assert not comparison['comparable']
    assert 'platform' in comparison['missing_conditions']


def test_author_annotations_are_filtered_but_never_copied_from_logs(source):
    doc = author()
    doc['requests'][0]['concern'] = 'key=' + SECRET + ' https://user:password@private.example/?token=hidden'
    doc['extra'] = PRIVATE_TASK
    exported = canonical(build_export(doc, source))
    assert SECRET.encode() not in exported and b'private.example' not in exported
    assert PRIVATE_TASK.encode() not in exported


def test_uncited_symlink_attempt_is_visible_but_not_read(source, tmp_path):
    outside = tmp_path / 'private-run'
    outside.mkdir()
    (outside / 'run_meta.json').write_text(PRIVATE_TASK)
    (source / 'linked').symlink_to(outside, target_is_directory=True)
    pack = build_export(author(), source)
    attempt = next(a for a in pack['manifest']['attempts'] if a['run_id'] == 'linked')
    assert attempt['status'] == 'invalid'
    assert not unit(pack, 'linked')['source_sha256']
    assert PRIVATE_TASK.encode() not in canonical(pack)


def test_missing_receipt_refuses_before_publishing_output(source, tmp_path):
    (source / 'sub/run_meta.json').unlink()
    doc_path = tmp_path / 'author.json'
    doc_path.write_text(json.dumps(author()))
    output = tmp_path / 'pack.json'
    result = subprocess.run([sys.executable, str(ROOT / 'tools/evidence_export.py'), '--manifest', str(doc_path),
                             '--runs-root', str(source), '--out', str(output)], capture_output=True, timeout=20)
    assert result.returncode == 2 and not output.exists()
    assert not list(tmp_path.glob('.evidence-*'))


def test_invalid_attempt_cannot_supply_variation_samples(source):
    save_run(source, 'repeat', 1, tree='b')
    doc = replica_document()
    doc['attempts'].append({'run_id': 'repeat', 'purpose': 'repeat', 'status': 'invalid',
                            'reason': 'operator discovered measurement contamination'})
    with pytest.raises(evidence.EvidenceError, match='invalid_variation_measurement'):
        build_export(doc, source)


def test_export_verifier_cross_checks_claims_even_with_updated_checksum(source):
    pack = build_export(author(), source)
    pack['manifest']['requests'][0]['result']['value'] = 0.9
    pack['content_sha256'] = digest(canonical({k: v for k, v in pack.items() if k != 'content_sha256'}))
    assert check_export(pack) == ['export_score_mismatch']


@pytest.mark.parametrize('mutate,problem', [
    (lambda p: p['comparisons'][0].update(score_delta=999), 'export_delta_mismatch'),
    (lambda p: p.update(comparisons=[]), 'export_comparison_inventory_mismatch'),
    (lambda p: p['manifest']['attempts'][0]['rounds'].append(99), 'export_attempt_round_mismatch'),
    (lambda p: p['comparisons'][0]['diff'].update(config=[]), 'export_diff_mismatch'),
])
def test_export_verifier_checks_derived_fields(source, mutate, problem):
    pack = build_export(author(), source)
    mutate(pack)
    pack['content_sha256'] = digest(canonical({k: v for k, v in pack.items() if k != 'content_sha256'}))
    assert check_export(pack) == [problem]


def test_export_verifier_checks_variation_arithmetic(source):
    save_run(source, 'repeat', 1, tree='b', rows=[[1, 1, 1], [1, 1, 1]])
    pack = build_export(replica_document(), source)
    assert not check_export(pack)
    pack['variation_groups'][0]['sample_variance'] = 999
    pack['content_sha256'] = digest(canonical({k: v for k, v in pack.items() if k != 'content_sha256'}))
    assert check_export(pack) == ['export_run_variation_mismatch']
