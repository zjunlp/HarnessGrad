"""Real static tools, trusted episode transport, and sandboxed CostBench controls."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from data.datasets import costbench as dataset
from eval.run import tool_relay
from eval.service.tool_transport import ToolTransportError, call


def asset(name):
    path = dataset.ASSETS / (name + '.py')
    spec = importlib.util.spec_from_file_location('costbench_test_' + name.replace('/', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared():
    if not dataset.provision_status()['present']:
        pytest.skip('CostBench preparation/full gold audit required')
    return dataset.roster()


def test_native_split_and_public_private_boundary(prepared, monkeypatch):
    assert len(prepared) == 2293
    assert len(dataset.SPLIT['train']) == 1912
    assert len(dataset.SPLIT['eval']) == 381
    assert dataset.OFFICIAL_EVAL == dataset.SPLIT['eval']
    assert set(dataset.SPLIT['train']).isdisjoint(dataset.SPLIT['eval'])
    original = Path.read_bytes
    def no_private(path, *args, **kwargs):
        assert path.name != 'private.json', 'metadata loading must not open private gold'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_bytes', no_private)
    tasks, scores = dataset.load()
    assert len(tasks) == len(scores) == 2293
    for split in ('train', 'test'):
        tid = next(tid for tid, row in prepared.items() if row['split'] == split)
        setup = dataset.SETUP[tid]
        public = json.dumps(setup['files'])
        assert all(s not in public for s in ('oracle_actions', 'groundtruth', 'private.json', 'search_space'))
        assert setup['tool_relay']['record']['path'] == 'trusted_episode'
        assert 'model' not in setup['tool_relay']


@pytest.mark.parametrize('split', ['train', 'test'])
@pytest.mark.parametrize('oracle', [True, False])
@pytest.mark.parametrize('platform', [True, False])
def test_gold_and_noop_real_controls(prepared, tmp_path, monkeypatch, split, oracle, platform):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    tid = next(tid for tid, row in prepared.items() if row['split'] == split)
    score, result = asset('control').run(oracle, platform, tid, tmp_path)
    assert score == float(oracle), result
    if platform:
        assert result['runtime_services']['tool_relay']['cleanup']['complete']
        evidence = result['verdict']['evidence']
    else:
        assert result['returncode'] == 0, result
        evidence = result['evidence']
    summary = evidence['official']['official']
    if oracle:
        assert summary['denominators']['cost'] == 1
        assert summary['metrics']['cost_gap_raw_units'] == pytest.approx(0)
        if split == 'test':
            assert summary['metrics']['official_tcr'] == 1
        else:
            assert summary['metrics']['train_compat_tcr'] == 1
    else:
        assert summary['denominators']['cost'] == 0
        assert summary['metrics']['cost_gap_raw_units'] is None


def start(tid):
    return tool_relay.start(dataset.SETUP[tid], context={
        'task_id': tid, 'run_id': 'costbench-test', 'trial_id': 'trial-1', 'seed': '19'})


@pytest.mark.parametrize('split', ['train', 'test'])
@pytest.mark.parametrize('domain', ['accommodation', 'transportation', 'attraction', 'dining', 'shopping'])
def test_nonlocation_gold_uses_official_environment(prepared, tmp_path, split, domain):
    tid = next(tid for tid, row in prepared.items() if row['split'] == split and row['domain'] == domain)
    score, result = asset('control').run(True, False, tid, tmp_path)
    assert score == 1, result
    metrics = result['evidence']['official']['official']['metrics']
    assert metrics['cost_gap_raw_units'] == pytest.approx(0)
    assert metrics['train_compat_tcr' if split == 'train' else 'official_tcr'] == 1


def test_corrupt_database_shard_is_infrastructure_failure(prepared, tmp_path):
    tid = 'costbench/test/Query00001'
    case = json.loads((dataset.PREPARED / prepared[tid]['path'] / 'private.json').read_text())
    setup = deepcopy(dataset.SETUP[tid])
    spec = json.loads(setup['tool_relay']['content'])
    index = tmp_path / case['search_space']
    index.parent.mkdir(parents=True)
    index.write_bytes((dataset.DATASET_ROOT / case['search_space']).read_bytes())
    location = index.parent / json.loads(index.read_text())['locations']['path']
    location.write_text('{}')
    spec['dataset_root'] = str(tmp_path)
    setup['tool_relay']['content'] = json.dumps(spec)
    relay = tool_relay.start(setup, context={'task_id': tid, 'run_id': 'shard-corruption',
                                           'trial_id': '1', 'seed': '0'})
    try:
        with pytest.raises(ToolTransportError, match='shard digest mismatch'):
            call(relay.path, 'turn', {'content': None, 'calls': [case['oracle_actions'][0]]})
        relay.stop()
        assert relay.fault(), 'corruption must not produce an ordinary scored trajectory'
    finally:
        relay.cleanup()


def test_state_survives_calls_and_actor_cannot_reset_or_score(prepared):
    tid = 'costbench/test/Query00001'
    row = prepared[tid]
    case = json.loads((dataset.PREPARED / row['path'] / 'private.json').read_text())
    relay = start(tid)
    try:
        for op in ('reset', 'episode', 'score', 'set_cost', 'seed', 'configure'):
            assert call(relay.path, op, {})['error'] == 'unsupported_operation_or_fields'
        for _ in range(25):
            assert call(relay.path, 'status', {})['steps'] == 0
        for malformed in ({}, False, 0, ''):
            assert call(relay.path, 'turn', {'content': None, 'calls': malformed})['error'] == 'calls_must_contain_name_and_arguments'
        assert call(relay.path, 'status', {})['steps'] == 0
        response = call(relay.path, 'turn', {'content': None, 'calls': [case['oracle_actions'][0]]})
        assert response['steps'] == 1 and not response['goal_reached']
        for _ in range(19):
            response = call(relay.path, 'turn', {'content': None, 'calls': [{'name': 'missing', 'arguments': {}}]})
        assert response['steps'] == 20 and response['termination'] == 'max_steps_reached'
        assert call(relay.path, 'turn', {'content': None, 'calls': [case['oracle_actions'][1]]})['error'] == 'episode_ended'
        relay.stop()
        assert relay.fault() is None
        record = relay.recording_input()
        sealed = json.loads((Path(record['from']) / 'episode.json').read_text())
        assert sealed['payload']['query_result']['total_steps'] == 20
        assert sealed['payload']['query_result']['is_goal_state'] is False
    finally:
        relay.cleanup()


def test_batch_executes_first_only_and_answer_precedes_tools(prepared):
    tid = 'costbench/test/Query00001'
    case = json.loads((dataset.PREPARED / prepared[tid]['path'] / 'private.json').read_text())
    relay = start(tid)
    try:
        response = call(relay.path, 'turn', {'content': None, 'calls': case['oracle_actions']})
        assert len(response['results']) == 2
        assert '[ERROR] Multiple tool calls' in response['results'][1]['content']
        assert not response['goal_reached'] and response['steps'] == 1
        response = call(relay.path, 'turn', {
            'content': '<answer> ' + case['oracle_answer'] + ' </answer>',
            'calls': [case['oracle_actions'][1]]})
        assert response['termination'] == 'final_answer'
        assert not response['results'] and not response['goal_reached']
    finally:
        relay.cleanup()


def test_private_case_corruption_refused_before_agent(prepared, tmp_path):
    tid = 'costbench/test/Query00001'
    setup = deepcopy(dataset.SETUP[tid])
    spec = json.loads(setup['tool_relay']['content'])
    fake = tmp_path / 'case.json'
    fake.write_text('{}')
    spec['case'] = str(fake)
    setup['tool_relay']['content'] = json.dumps(spec)
    with pytest.raises(RuntimeError, match='digest mismatch'):
        tool_relay.start(setup, context={'task_id': tid, 'run_id': 'corruption', 'trial_id': '1', 'seed': '0'})


def test_actor_cannot_read_private_inputs_or_forge_verifier(prepared, tmp_path, monkeypatch):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    score, result = asset('control').run(False, True, 'costbench/test/Query00001', tmp_path, attack=True)
    assert score == 0, result
    assert result['verdict']['evidence']['measured'] is True
    assert 'forged' not in json.dumps(result['verdict'])
    assert result['runtime_services']['tool_relay']['cleanup']['complete']


def test_runtime_failure_is_unmeasured_not_task_zero(prepared, tmp_path):
    from eval.verification.verifier import _verify
    tid = 'costbench/test/Query00001'
    spec = deepcopy(dataset.VERIFY[tid])
    row = prepared[tid]
    spec['inputs'].append({'path': 'trusted_episode/episode.json', 'content': json.dumps({
        'schema': 'harnessgrad.interaction-record.v1',
        'binding': {'task_id': tid, 'seed': '0'},
        'payload': {'task_id': tid, 'seed': '0', 'case_sha256': row['private_sha256'],
                    'revision': dataset.REVISION, 'failure': 'FileNotFoundError'}})})
    result = _verify(tmp_path, spec, '', sandbox=True, timeout_s=30)
    assert result['score'] is None and result['outcome'] == 'unmeasured', result
