"""A valid H0 does not turn an incomplete RSI experiment into a result row."""
import json
from pathlib import Path

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
