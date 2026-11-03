"""A valid H0 does not turn an incomplete RSI experiment into a result row."""
import json
from pathlib import Path

import pytest

from tools import results_matrix, run_summary


def record(root, name, status):
    run = root / name
    run.mkdir()
    meta = {'run_id': name, 'dataset': 'demo', 'harness': 'loop',
        'method_entrypoint': 'python /example/methods/dgm/run.py',
        'split_protocol': {'kind': 'custom-heldout', 'official_eval_reused_for_train': 3},
        'split_sha': 'a' * 64,
        'completion': {'status': status, 'requested_rounds': 3, 'completed_rounds': 0,
            'reason': 'provider failed' if status == 'incomplete' else 'method explicitly stopped'}}
    point = {'run_id': name, 'round': 0, 'side': 'train', 'score': .25,
        'per_task': {'task-a': .25}, 'n_scored': 1, 'n_invalid': 0,
        'identity': {'harness_name': 'loop', 'method': 'dgm'},
        'sampling': {'dataset': 'demo', 'policy': 'all', 'task_ids': ['task-a']}}
    (run / 'run_meta.json').write_text(json.dumps(meta))
    (run / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    return run


def test_matrix_excludes_failed_h0_but_preserves_graceful_stop(tmp_path):
    record(tmp_path, 'failed-study', 'incomplete')
    record(tmp_path, 'graceful-stop', 'early_stop')
    payload, _ = results_matrix.build_matrix([str(tmp_path)])
    assert [r['run_id'] for r in payload['rows']] == ['graceful-stop']
    assert payload['rows'][0]['partition'] == 'custom-heldout'
    assert payload['rows'][0]['split_sha'] == 'a' * 64
    assert [(s['run_id'], s['reason']) for s in payload['skipped']] == [('failed-study', 'incomplete')]
    assert 'score' not in payload['skipped'][0]
    table = results_matrix.render_markdown(payload)
    assert 'custom-heldout' in table and 'early_stop' in table
    assert 'provider failed' in table


def test_summary_keeps_valid_score_and_prominent_incomplete_status(tmp_path):
    run = record(tmp_path, 'failed-study', 'incomplete')
    result = run_summary.summarise(run)
    assert result['score']['last'] == .25
    assert result['completion']['status'] == 'incomplete'
    page = run_summary.render_markdown(result)
    assert '**INCOMPLETE**: 0/3 improvement rounds' in page
    assert 'provider failed' in page
    assert 'not an official benchmark/leaderboard score' in page


def test_legacy_absence_is_not_a_fabricated_complete_status(tmp_path):
    run = record(tmp_path, 'old-study', 'complete')
    path = run / 'run_meta.json'
    meta = json.loads(path.read_text())
    del meta['completion']
    path.write_text(json.dumps(meta))
    result = run_summary.summarise(run)
    assert result['completion'] is None
    assert '**COMPLETE**' not in run_summary.render_markdown(result)


def test_incomplete_skip_retains_measurement_context_and_coverage(tmp_path):
    run = record(tmp_path, 'incomplete-context', 'incomplete')
    point = json.loads((run / 'curve.jsonl').read_text())
    point['sampling']['task_ids'].append('task-b')
    point['baseline'] = {'comparable': False, 'problems': ['fixture endpoint mismatch']}
    point['measured_by_platform'] = True
    (run / 'curve.jsonl').write_text(json.dumps(point) + '\n')

    payload, _ = results_matrix.build_matrix([str(tmp_path)])
    assert payload['rows'] == []
    skipped, = payload['skipped']
    assert skipped['reason'] == 'incomplete' and 'provider failed' in skipped['detail']
    assert 'score' not in skipped
    context, = skipped['measurement_context']
    assert context['planned_tasks'] == 2 and context['n_tasks'] == 1 and context['unmeasured'] == 1
    assert context['baseline'] == point['baseline']
    assert context['split_sha'] == 'a' * 64
    assert context['split_protocol']['kind'] == 'custom-heldout'
    summary = run_summary.summarise(run)
    assert summary['completion']['status'] == 'incomplete'
    assert summary['per_round'][0]['planned'] == 2
    assert '**INCOMPLETE**' in run_summary.render_markdown(summary)


def test_recorded_early_stop_keeps_status_but_rejected_placeholder_is_not_a_score(tmp_path):
    run = record(tmp_path, 'early-stop-rejection', 'early_stop')
    first = json.loads((run / 'curve.jsonl').read_text())
    first['measured_by_platform'] = True
    # The actual rejected-candidate shape written by mode_a: numeric placeholder,
    # empty measurement coverage, false measurement flag, and structured refusal.
    rejected = {**first, 'round': 1, 'score': 0.0, 'per_task': {}, 'n_scored': 0,
        'measured_by_platform': False, 'unmeasured': {
            'reason': 'the candidate harness is not one the platform can measure, so there is no measurement to report',
            'problems': ['missing agent.py'], 'harness_dir': '/fixture/rejected'}}
    (run / 'curve.jsonl').write_text(json.dumps(first) + '\n' + json.dumps(rejected) + '\n')

    payload, _ = results_matrix.build_matrix([str(tmp_path)])
    assert payload['skipped'] == []
    row, = payload['rows']
    assert row['completion']['status'] == 'early_stop'
    assert row['partition'] == 'custom-heldout' and row['split_sha'] == 'a' * 64
    assert row['rounds'] == 1 and row['r0'] == row['last'] == row['best'] == .25
    assert row['last_delta'] is None
    assert row['measurement_context'][-1]['unmeasured_reason'] == rejected['unmeasured']
    markdown = results_matrix.render_markdown(payload)
    assert 'early_stop' in markdown and 'partial' in markdown and 'missing agent.py' in markdown
    summary = run_summary.summarise(run)
    assert summary['completion']['status'] == 'early_stop'
    assert summary['score']['last'] is None and summary['score']['best'] == .25
    assert summary['per_round'][-1]['scored'] == 0
    assert '**EARLY_STOP**' in run_summary.render_markdown(summary)


@pytest.mark.parametrize('surface', ['matrix', 'summary'])
def test_unknown_metadata_protocol_does_not_invent_custom_partition(tmp_path, surface):
    run = record(tmp_path, 'unknown-meta-protocol', 'early_stop')
    path = run / 'run_meta.json'
    metadata = json.loads(path.read_text())
    metadata['split_protocol'] = {'official_score_comparable': False}
    path.write_text(json.dumps(metadata))
    if surface == 'matrix':
        payload, _ = results_matrix.build_matrix([str(tmp_path)])
        row, = payload['rows']
        assert row['partition'] is None
        line = next(line for line in results_matrix.render_markdown(payload).splitlines()
                    if line.startswith('| unknown-meta-protocol'))
        assert 'custom-heldout' not in line
        assert 'unknown split protocol' in line and 'noncomparable to official score' in line
    else:
        summary = run_summary.summarise(run)
        page = run_summary.render_markdown(summary)
        assert 'Custom held-out partition' not in page
        assert 'Split protocol: unknown' in page
        assert 'not comparable to the official benchmark/leaderboard score' in page.lower()
