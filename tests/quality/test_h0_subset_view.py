"""Verified subset views use existing evidence, never infer missing measurements."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from harnessgrad.provenance import verification_context
from harnessgrad.orchestration import h0
from harnessgrad.orchestration.h0_subset import materialize_subset
from harnessgrad.provenance import measurement_components
from test_h0_planning import control, donor


def prepared(tmp_path, control, count=40):
    work, manifest, options, identities, attribution, reporter = control
    root, point = donor(tmp_path, control)
    ids = [f'task/{i}' for i in range(count)]
    context = verification_context.create({tid: f'{i:064x}' for i, tid in enumerate(ids)})
    scores = {tid: float(i % 2) for i, tid in enumerate(ids)}
    point.update(per_task=scores, verification_context=context, sampling={'task_ids': ids}, score=.5)
    (root / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    metadata = json.loads((root / 'run_meta.json').read_text())
    metadata['verification_context'] = context
    (root / 'run_meta.json').write_text(json.dumps(metadata))
    base = {'per_task': scores, 'verification_context': context, 'trials': 1}
    (root / 'h0.json').write_text(json.dumps({'h0_sha': 'owned-sha', 'verification_context': context, 'base': base}))
    for tid in ids:
        trace = root / 'round0_traces' / (tid + '.jsonl')
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text('selected evidence ' + tid)
        receipt = root / 'verdicts/round-0' / (tid + '.json')
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({'task_id': tid, 'round': 0, 'score': scores[tid],
            'passed': bool(scores[tid]), 'kind': 'answer', 'harness': {'stdout': tid}}))
    return root, ids, context


def derive(tmp_path, control, selected, context):
    work, manifest, options, identities, attribution, _ = control
    identities = replace(identities, verification=verification_context.project(context, selected))
    loaded = h0.load_h0_donor('donor', tmp_path, 'owned-sha')
    return materialize_subset(loaded, work, 'owned-sha', manifest, attribution, options,
                              identities, selected, setups={}, envs={})


@pytest.mark.parametrize('size', [20, 16])
def test_forty_task_original_has_exact_selected_view(control, tmp_path, size):
    root, ids, context = prepared(tmp_path, control)
    selected = ids[:size - 1] + [ids[-2]]  # Deliberately differs from donor aggregate.
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    shared = derive(tmp_path, control, selected, context)
    expected = {tid: float(ids.index(tid) % 2) for tid in selected}
    assert shared.point['per_task'] == shared.base['per_task'] == expected
    assert shared.point['score'] == sum(expected.values()) / size
    assert shared.point['sampling']['task_ids'] == selected
    assert shared.point['n_scored'] == size
    for key in ('traces', 'verdicts', 'harness_output'):
        assert set(shared.base[key]) == set(selected)
    assert shared.point['cost'] == {'harness_tokens': None, 'env_generation_tokens': None}
    assert shared.point['h0_subset_view']['new_evaluation_trials'] == 0
    assert shared.point['h0_subset_view']['source_files_sha256']['h0.json']
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_subset_checks_independent_measurement_components(control, tmp_path):
    root, ids, context = prepared(tmp_path, control)
    bundle = {'data_source': measurement_components.data_source('synthetic', origin='source', revision='v1'),
              'loader': measurement_components.implementation('loader', {'provider': 'a' * 64}),
              'scorer': measurement_components.implementation('scorer', {'verify': 'b' * 64})}
    for filename in ('curve.jsonl', 'run_meta.json', 'h0.json'):
        path = root / filename
        value = json.loads(path.read_text())
        value['measurement_components'] = bundle
        if filename == 'h0.json':
            value['base']['measurement_components'] = bundle
        path.write_text(json.dumps(value) + ('\n' if filename.endswith('jsonl') else ''))
    work, manifest, options, identities, attribution, _ = control
    selected = ids[:16]
    identities = replace(identities, verification=verification_context.project(context, selected), measurement=bundle)
    def load():
        return h0.load_h0_donor('donor', tmp_path, 'owned-sha')
    shared = materialize_subset(load(), work, 'owned-sha', manifest, attribution, options,
                                identities, selected, setups={}, envs={})
    assert shared.point['measurement_components'] == bundle
    metadata = root / 'run_meta.json'
    value = json.loads(metadata.read_text())
    del value['measurement_components']
    metadata.write_text(json.dumps(value))
    with pytest.raises(h0.H0Refused, match='measurement_components missing'):
        materialize_subset(load(), work, 'owned-sha', manifest, attribution, options,
                           identities, selected, setups={}, envs={})


@pytest.mark.parametrize('damage', ['context', 'score', 'trace', 'verdict', 'source', 'envelope', 'runtime'])
def test_missing_or_changed_evidence_refuses(control, tmp_path, damage):
    root, ids, context = prepared(tmp_path, control)
    if damage in ('trace', 'verdict'):
        (root / ('round0_traces' if damage == 'trace' else 'verdicts/round-0') /
         (ids[0] + ('.jsonl' if damage == 'trace' else '.json'))).unlink()
    elif damage == 'context':
        context = verification_context.create({**context['tasks'], ids[0]: 'f' * 64})
    else:
        path = root / ('curve.jsonl' if damage == 'source' else 'h0.json')
        value = json.loads(path.read_text())
        if damage == 'score': value['base']['per_task'][ids[0]] = .9
        if damage == 'source': value['identity']['harness_sha'] = 'different'
        if damage == 'envelope': value['verification_context']['sha256'] = '0' * 64
        if damage == 'runtime': value['base']['dataset_runtime_identity'] = {'bad': 'envelope'}
        path.write_text(json.dumps(value))
    with pytest.raises(h0.H0Refused):
        derive(tmp_path, control, ids[:16], context)


@pytest.mark.parametrize('selection', ['same', 'empty', 'duplicate', 'missing'])
def test_selection_must_be_explicit_proper_subset(control, tmp_path, selection):
    _, ids, context = prepared(tmp_path, control)
    selected = {'same': ids, 'empty': [], 'duplicate': [ids[0], ids[0]], 'missing': ['absent']}[selection]
    # Project normally rejects duplicates/missing too; reach the subset gate directly.
    work, manifest, options, identities, attribution, _ = control
    with pytest.raises(h0.H0Refused):
        materialize_subset(h0.load_h0_donor('donor', tmp_path, 'owned-sha'), work,
            'owned-sha', manifest, attribution, options, identities, selected, setups={}, envs={})


def test_real_driver_mock_actor_subset_and_no_change_round(tmp_path):
    root = Path(__file__).resolve().parents[2]
    def execute(name, *extra):
        return subprocess.run([sys.executable, 'driver.py', '--harness', 'loop_plain',
            '--dataset', 'demo', '--sampling', 'all', '--rounds', '1' if name == 'view' else '0',
            '--jobs', '1', '--no-sandbox', '--improver', 'none', '--no-summary', '--run-id', name,
            '--runs-root', str(tmp_path / 'runs'), '--work-root', str(tmp_path / ('work-' + name)),
            '--method-entrypoint', f'{sys.executable} {root / "methods/noop/run.py"}', *extra],
            cwd=root, env={**os.environ, 'HG_AGENT_BACKEND': 'mock', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'},
            capture_output=True, text=True, timeout=120)
    original = execute('original')
    assert original.returncode == 0, original.stdout[-1500:] + original.stderr[-1500:]
    donor_root = tmp_path / 'runs/original'
    before = {p.relative_to(donor_root): p.read_bytes() for p in donor_root.rglob('*') if p.is_file()}
    view = execute('view', '--h0-from', 'original', '--h0-subset-view', '--tasks', 't01,t02')
    assert view.returncode == 0, view.stdout[-2000:] + view.stderr[-2000:]
    curve = [json.loads(line) for line in (tmp_path / 'runs/view/curve.jsonl').read_text().splitlines()]
    assert len(curve) == 2
    assert set(curve[0]['per_task']) == {'t01', 't02'}
    assert curve[1]['per_task'] == curve[0]['per_task']
    assert curve[1]['score'] == curve[0]['score']
    assert curve[0]['current_run_cost']['evaluation_trials'] == 0
    assert not (tmp_path / 'runs/view/progress/round-0').exists()
    assert before == {p.relative_to(donor_root): p.read_bytes() for p in donor_root.rglob('*') if p.is_file()}
    resumed = execute('view', '--resume', '--tasks', 't01,t02', '--rounds', '2')
    assert resumed.returncode == 0, resumed.stdout[-1500:] + resumed.stderr[-1500:]
    continued = [json.loads(line) for line in (tmp_path / 'runs/view/curve.jsonl').read_text().splitlines()]
    assert len(continued) == 3
    assert all(set(point['per_task']) == {'t01', 't02'} for point in continued)
    assert continued[0] == curve[0]


def test_selected_trial_spread_and_test_counts_are_reduced_from_receipts(control, tmp_path):
    root, ids, context = prepared(tmp_path, control)
    path = root / 'h0.json'
    payload = json.loads(path.read_text())
    payload['base'].update(trials=2, per_task_trials={})
    point = json.loads((root / 'curve.jsonl').read_text())
    point['n_trials'] = 2
    point['per_task_trials'] = {}
    for index, tid in enumerate(ids[:2]):
        values = [0., 1.] if index == 0 else [1., 1.]
        value = sum(values) / 2
        payload['base']['per_task'][tid] = point['per_task'][tid] = value
        payload['base']['per_task_trials'][tid] = point['per_task_trials'][tid] = values
        receipt = root / 'verdicts/round-0' / (tid + '.json')
        record = json.loads(receipt.read_text())
        record.update(score=value, trials=[{'score': score, 'passed': bool(score),
            'tests': {'passed': int(score), 'failed': 1 - int(score)}} for score in values],
            trial_scores=values)
        receipt.write_text(json.dumps(record))
        for trial in range(2):
            (root / 'round0_traces' / (tid + f'.trial{trial}.jsonl')).write_text('trial evidence')
    path.write_text(json.dumps(payload))
    (root / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    parts = list(control)
    parts[2] = replace(parts[2], trials=2)
    shared = derive(tmp_path, tuple(parts), ids[:2], context)
    assert shared.point['score'] == .75
    assert shared.point['score_std'] == .25
    assert shared.point['per_task_trials'] == {ids[0]: [0., 1.], ids[1]: [1., 1.]}
    assert shared.point['tests_passed'] == 3 and shared.point['tests_total'] == 4
    assert shared.point['test_rate'] == .75
    assert set(shared.base['trial_traces']) == set(ids[:2])


@pytest.mark.parametrize('field', ['endpoint', 'agent_model', 'agent_backend', 'scorer'])
def test_subset_retains_measurement_identity_gates(control, tmp_path, field):
    root, ids, context = prepared(tmp_path, control)
    path = root / 'run_meta.json'
    value = json.loads(path.read_text())
    if field == 'scorer':
        value['scoring_identity'] = {'changed': True}
    else:
        key = {'endpoint': 'base_url', 'agent_model': 'model', 'agent_backend': 'backend'}[field]
        value['config']['agent'][key] = 'changed'
    path.write_text(json.dumps(value))
    with pytest.raises(h0.H0Refused):
        derive(tmp_path, control, ids[:16], context)


def test_unselected_content_does_not_replace_selected_verification(control, tmp_path):
    _, ids, context = prepared(tmp_path, control)
    current = verification_context.create({**context['tasks'], ids[-1]: 'f' * 64})
    shared = derive(tmp_path, control, ids[:16], current)
    assert shared.base['verification_context'] == verification_context.project(context, ids[:16])


@pytest.mark.parametrize('extra', [[], ['--side', 'eval'], ['--sampling', 'below_0.5']])
def test_invalid_subset_cli_is_refused_before_execution(tmp_path, extra):
    root = Path(__file__).resolve().parents[2]
    args = [sys.executable, 'driver.py', '--h0-subset-view', '--tasks', 't01', '--h0-from', 'donor']
    if extra:
        args += ['--sampling', 'all', *extra]
    proc = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert '--h0-subset-view requires' in proc.stderr
