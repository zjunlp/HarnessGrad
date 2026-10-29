"""Rejected actor RPCs preserve CostBench scoring without hiding environment faults."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

from data.datasets import costbench, costbench_dynamic


ACTOR = r'''
import argparse, json, socket, sys
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--task'); parser.add_argument('--workdir')
work = Path(parser.parse_args().workdir)
sys.path.insert(0, str(work))
from costbench_transport import call
sock = '/run/harnessgrad-tool/tool.sock'
fixture = json.loads((work / '.oracle/control.json').read_text())

def invalid_requests():
    for frame, error in [(b'{not-json}', 'bad_request'), (b'[]', 'bad_request'),
                         (b'{"op": 1}', 'bad_op'),
                         (b'{"op": "turn", "args": [1]}', 'bad_args')]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(sock)
            client.sendall(frame + b'\n')
            assert json.loads(client.recv(4096)) == {'ok': False, 'error': error}

invalid_requests()
assert call(sock, 'status')['steps'] == 0
if fixture['outcome'] in ('success', 'runtime', 'budget'):
    for action in fixture['actions']:
        call(sock, 'turn', {'content': None, 'calls': [action]})
    call(sock, 'turn', {'content': '<answer> ' + fixture['answer'] + ' </answer>', 'calls': []})
elif fixture['outcome'] == 'invalid_tool':
    reply = call(sock, 'turn', {'content': None, 'calls': [
        {'name': 'nonexistent_fixture_tool', 'arguments': {}}]})
    assert reply['results'][0]['content'].startswith('[ERROR]')
    call(sock, 'turn', {'content': '<answer> 00000 </answer>', 'calls': []})
invalid_requests()
'''


CASES = [(mode, 'success') for mode in ('static', *costbench_dynamic.MODES)] + [
    (mode, outcome) for mode in ('static', 'cost_change')
    for outcome in ('invalid_tool', 'incomplete', 'runtime', 'budget')]


@pytest.mark.parametrize('mode,outcome', CASES)
def test_rejected_requests_preserve_scoring_and_real_failures(tmp_path, monkeypatch, mode, outcome):
    from eval.run.runner import run_one

    dataset = costbench if mode == 'static' else costbench_dynamic
    if not dataset.provision_status()['present']:
        pytest.skip('full pinned CostBench preparation required')
    tid = ('costbench/test/Query00001' if mode == 'static'
           else f'costbench_dynamic/{mode}/test/Query00001')
    case = json.loads((dataset.PREPARED / dataset.roster()[tid]['path'] / 'private.json').read_text())
    harness = tmp_path / 'harness'
    harness.mkdir()
    (harness / 'agent.py').write_text(ACTOR)
    (harness / 'harness.json').write_text(json.dumps({
        'entrypoint': 'agent.py',
        'runtime_paths': list(dict.fromkeys([sys.prefix, sys.base_prefix,
            str(Path(sys._base_executable).parent.parent)])),
    }))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''))
    setup = deepcopy(dataset.SETUP[tid])
    setup['files'].append({'path': '.oracle/control.json', 'content': json.dumps({
        'outcome': outcome, 'actions': case['oracle_actions'], 'answer': case['oracle_answer'],
    })})
    if outcome == 'runtime':
        # The index still has its pinned digest; the first real lookup discovers
        # a corrupted shard, exercising a handler exception rather than a request error.
        database = tmp_path / 'corrupt-database'
        index = database / case['search_space']
        index.parent.mkdir(parents=True)
        index.write_bytes((dataset.DATASET_ROOT / case['search_space']).read_bytes())
        shard = index.parent / json.loads(index.read_text())['locations']['path']
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_text('{}')
        spec = json.loads(setup['tool_relay']['content'])
        spec['dataset_root'] = str(database)
        setup['tool_relay']['content'] = json.dumps(spec)
    elif outcome == 'budget':
        # The initial status consumes this explicit test budget; the first turn
        # must be refused even though malformed envelopes do not consume calls.
        setup['tool_relay']['max_calls'] = 1

    result = run_one(harness, dataset.control_task(tid), setup=setup,
                     verifier=dataset.VERIFY[tid], env=dataset.ENV[tid],
                     run_id='costbench-request-errors', trial_id=f'{mode}-{outcome}',
                     run_seed='42', recordings_root=tmp_path / 'recordings', sandbox=True)
    relay = result['runtime_services']['tool_relay']
    assert relay['cleanup']['complete'], result
    transport = relay['receipt']['transport']
    if outcome in ('runtime', 'budget'):
        assert result['invalid']['stage'] == 'tool relay', result
        assert result.get('verdict') is None
        assert relay['faults'] and relay['recording'] is None
        assert transport['request_errors'] == 4
        assert transport['errors'] == (5 if outcome == 'runtime' else 4)
        assert transport['refused'] == (1 if outcome == 'budget' else 0)
        return

    assert 'invalid' not in result and result['exit_code'] == 0, result
    assert not relay['faults']
    assert transport['errors'] == transport['request_errors'] == 8
    assert transport['refused'] == 0
    sealed = json.loads(Path(relay['recording']['archive']).read_text())
    assert sealed['binding']['trial_id'] == f'{mode}-{outcome}'
    episode = sealed['payload']
    assert episode['failure'] is None
    assert episode['query_result']['total_steps'] == (
        len(case['oracle_actions']) + 1 if outcome == 'success' else 2 if outcome == 'invalid_tool' else 0)
    assert episode['termination'] == ('actor_stopped' if outcome == 'incomplete' else 'final_answer')
    verdict = result['verdict']
    assert verdict['evidence']['measured'] is True
    assert verdict['score'] == float(outcome == 'success')
    metric = dataset.METRIC if mode == 'static' else dataset.metric(mode)
    assert verdict['evidence']['metric'] == metric
