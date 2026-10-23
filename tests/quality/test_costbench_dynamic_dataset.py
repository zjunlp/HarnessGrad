"""Prepared dynamic cases through the sealed relay, verifier and sandbox."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from data.datasets import costbench_dynamic as dataset
from eval.run import tool_relay
from eval.service.tool_transport import call


def control():
    path = dataset.ASSETS.parent / 'costbench_dynamic/control.py'
    spec = importlib.util.spec_from_file_location('costbench_dynamic_control_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared():
    if not dataset.provision_status()['present']:
        pytest.skip('full pinned dynamic preparation required')
    return dataset.roster()


def sample(rows, mode='cost_change', split='test', domain='location'):
    return next(tid for tid, row in rows.items()
                if row['mode'] == mode and row['split'] == split and row['domain'] == domain)


@pytest.mark.parametrize('name', ['costbench', 'costbench_dynamic'])
def test_standard_run_mean_uses_explicit_costbench_policy(name, tmp_path, monkeypatch):
    from data.scaffolds import aggregation
    from eval.run.metrics import mean
    assert mean([1.0, 0.0], dataset=name) == 0.5
    assert mean([], dataset=name) == 0.0
    policies = json.loads(aggregation.POLICIES.read_text())
    policies[name]['run_reward']['denominator'] = 'gold_rows'
    changed = tmp_path / 'policies.json'
    changed.write_text(json.dumps(policies))
    monkeypatch.setattr(aggregation, 'POLICIES', changed)
    with pytest.raises(ValueError, match='gold_rows needs a nonnegative count'):
        mean([1.0, 0.0], dataset=name)


def test_catalog_and_private_event_boundary(prepared, monkeypatch):
    assert len(prepared) == 9172
    assert len(dataset.SPLIT['train']) == 7648
    assert len(dataset.SPLIT['eval']) == 1524
    assert set(dataset.SPLIT['train']).isdisjoint(dataset.SPLIT['eval'])
    assert dataset.OFFICIAL_EVAL == dataset.SPLIT['eval']
    original = Path.read_bytes
    def no_private(path, *args, **kwargs):
        assert path.name != 'private.json'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_bytes', no_private)
    tasks, scores = dataset.load()
    assert len(tasks) == len(scores) == 9172
    assert set(scores.values()) == {dataset.metric(mode) for mode in dataset.MODES}
    for mode in dataset.MODES:
        for split in ('train', 'test'):
            tid = sample(prepared, mode, split)
            setup = dataset.SETUP[tid]
            text = json.dumps(setup['files'])
            assert all(key not in text for key in ('oracle_actions', 'groundtruth', 'blocking_plan',
                                                   'trigger_step', 'new_random_seed', 'private.json'))
            assert setup['tool_relay']['record']['path'] == 'trusted_episode'


def test_complete_dynamic_gold_audit_is_bound_to_catalog(prepared):
    raw = (dataset.PREPARED / 'gold-audit.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == dataset.manifest()['gold_audit_sha256']
    audit = json.loads(raw)
    assert audit['revision'] == dataset.REVISION
    assert audit['smoke'] is False
    assert audit['total'] == audit['passed'] == len(prepared)
    rows = audit['results']
    assert len(rows) == len({r['task_id'] for r in rows}) == len(prepared)
    assert {r['task_id'] for r in rows} == set(prepared)
    assert all(r['passed'] and r['answer_correct'] and r['path_exact_match']
               and r['block_count'] == 1 for r in rows)
    assert all(abs(r['agent_cost'] - r['gt_cost']) < 1e-8 for r in rows)


@pytest.mark.parametrize('mode', dataset.MODES)
@pytest.mark.parametrize('split', ['train', 'test'])
@pytest.mark.parametrize('oracle', [True, False])
@pytest.mark.parametrize('platform', [True, False])
def test_dynamic_gold_noop_through_real_platform(prepared, tmp_path, monkeypatch, mode, split, oracle, platform):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    tid = sample(prepared, mode, split)
    value, result = control().run(oracle, platform, tid, tmp_path)
    assert value == float(oracle), result
    evidence = result['verdict']['evidence'] if platform else result['evidence']
    if platform:
        assert result['runtime_services']['tool_relay']['cleanup']['complete']
    else:
        assert result['returncode'] == 0
    assert evidence['measured'] and evidence['metric'] == dataset.metric(mode)
    report = evidence['official']['official']
    assert report['primary_denominator'] == 1
    if oracle:
        assert report['event_incomplete_count'] == 0
        assert report['denominators']['cost'] == 1
        assert report['metrics']['cost_gap_raw_units'] == pytest.approx(0)
        assert report['metrics']['train_compat_tcr' if split == 'train' else 'official_tcr'] == 1
    else:
        assert report['event_incomplete_count'] == 1
        assert report['denominators']['cost'] == 0
        assert report['metrics']['cost_gap_raw_units'] is None


@pytest.mark.parametrize('mode', dataset.MODES)
@pytest.mark.parametrize('domain', ['location', 'dining'])
@pytest.mark.parametrize('split', ['train', 'test'])
def test_live_show_and_actor_cannot_schedule_or_reset(prepared, mode, domain, split):
    tid = sample(prepared, mode, split, domain)
    case = json.loads((dataset.PREPARED / prepared[tid]['path'] / 'private.json').read_text())
    relay = tool_relay.start(dataset.SETUP[tid], context={
        'task_id': tid, 'run_id': 'dynamic-visibility', 'trial_id': '1', 'seed': '17'})
    try:
        initial = call(relay.path, 'show', {})
        assert call(relay.path, 'status', {})['steps'] == 0
        for op in ('reset', 'episode', 'score', 'schedule_event', 'set_cost', 'seed'):
            assert 'error' in call(relay.path, op, {})
        trigger = case['query']['block_stats']['block_step'][0]
        replies = []
        for action in case['oracle_actions'][:trigger]:
            replies.append(call(relay.path, 'turn', {'content': None, 'calls': [action]}))
        current = call(relay.path, 'show', {})
        assert call(relay.path, 'status', {})['steps'] == trigger
        assert all(key not in json.dumps([current, replies]) for key in
                   ('blocking_plan', 'groundtruth', 'new_random_seed', 'trigger_step', 'oracle_actions'))
        if mode in ('cost_change', 'steplen_change'):
            assert initial['tools'] != current['tools']
            assert replies[-1]['tools'] == current['tools']
        elif mode == 'preference_change':
            assert initial['query'] != current['query']
            assert replies[-1]['messages'][-1]['content'] == current['query']
            assert 'previously provided TimeInfo information is still valid' in current['query']
            if domain != 'location':
                replacement = case['query']['blocking_plan'][0]['parameters']['location']
                assert replacement in current['query']
        else:
            assert current == initial
            assert '[ERROR]' in replies[-1]['results'][0]['content']
        relay.stop()
        assert relay.fault() is None
        record = Path(relay.recording_input()['from']) / 'episode.json'
        query = json.loads(record.read_text())['payload']['query_result']
        assert query['TimeInfo'] == case['query']['TimeInfo']
    finally:
        relay.cleanup()


def test_forged_actor_records_cannot_replace_dynamic_evidence(prepared, tmp_path, monkeypatch):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    score, result = control().run(False, True, sample(prepared), tmp_path, attack=True)
    assert score == 0, result
    assert result['verdict']['evidence']['measured']
    assert 'forged' not in json.dumps(result['verdict'])
    assert result['runtime_services']['tool_relay']['cleanup']['complete']


def test_corrupt_private_plan_refused(prepared, tmp_path):
    tid = sample(prepared)
    setup = deepcopy(dataset.SETUP[tid])
    spec = json.loads(setup['tool_relay']['content'])
    fake = tmp_path / 'case.json'
    fake.write_text('{}')
    spec['case'] = str(fake)
    setup['tool_relay']['content'] = json.dumps(spec)
    with pytest.raises(RuntimeError, match='digest mismatch'):
        tool_relay.start(setup, context={'task_id': tid, 'run_id': 'corruption', 'trial_id': '1', 'seed': '0'})
