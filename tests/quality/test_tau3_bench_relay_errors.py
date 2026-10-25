"""Malformed actor RPCs preserve healthy τ³ episodes and genuine failures."""
import json
import sys
from pathlib import Path

import pytest

from data.datasets import tau3_bench as dataset

if not (dataset.DATASET_ROOT / 'manifest.json').is_file():
    pytest.skip('prepare pinned tau3 data and isolated runtime', allow_module_level=True)

from test_tau3_bench_dataset import control_module


ACTOR = '''
import argparse, json, socket, sys
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--task'); p.add_argument('--workdir')
work = Path(p.parse_args().workdir)
sys.path.insert(0, str(work))
from tau3_transport import call
sock = '/run/harnessgrad-tool/tool.sock'
fixture = json.loads((work / '.oracle/control.json').read_text())
def invalid_requests():
    for frame, error in [(b'{not-json}', 'bad_request'), (b'[]', 'bad_request'),
                         (b'{"op": 1}', 'bad_op'),
                         (b'{"op": "call", "args": [1]}', 'bad_args')]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(sock)
            client.sendall(frame + b'\\n')
            assert json.loads(client.recv(4096)) == {'ok': False, 'error': error}
invalid_requests()
call(sock, 'start', timeout_s=30)
if fixture['mode'] == 'success':
    for action in fixture['actions']:
        call(sock, 'call', {'tool_name': action['name'], 'arguments': action['arguments']})
    call(sock, 'say', {'message': '###STOP###'})
elif fixture['mode'] == 'official_limit':
    for _ in range(10):
        call(sock, 'call', {'tool_name': 'nonexistent_fixture_tool', 'arguments': {}})
elif fixture['mode'] == 'budget':
    call(sock, 'status')
invalid_requests()
'''


@pytest.mark.parametrize('mode', ['success', 'user_stop', 'official_limit', 'incomplete', 'provider', 'budget'])
def test_rejected_actor_requests_preserve_official_scoring(tmp_path, monkeypatch, mode):
    from eval.run.runner import run_one

    tid = 'tau3_bench/airline/8'
    harness = tmp_path / 'harness'
    harness.mkdir()
    (harness / 'agent.py').write_text(ACTOR)
    (harness / 'harness.json').write_text(json.dumps({
        'entrypoint': 'agent.py',
        'runtime_paths': list(dict.fromkeys([sys.prefix, sys.base_prefix,
            str(Path(sys._base_executable).parent.parent)])),
    }))
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    with control_module().Endpoint() as endpoint:
        if mode == 'provider':
            def unavailable(handler):
                handler.send_error(503, 'fixture provider unavailable')
            monkeypatch.setattr(endpoint.server.RequestHandlerClass, 'do_POST', unavailable)
        elif mode == 'user_stop':
            def stopped_user(handler):
                handler.rfile.read(int(handler.headers['Content-Length']))
                body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': '###STOP###'}}],
                                   'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}).encode()
                handler.send_response(200)
                handler.send_header('Content-Length', str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)
            monkeypatch.setattr(endpoint.server.RequestHandlerClass, 'do_POST', stopped_user)
        setup = dataset.SETUP[tid]
        config = json.loads((dataset.DATASET_ROOT / dataset.roster()[tid]['path'] / 'private.json').read_text())
        setup['files'].append({'path': '.oracle/control.json', 'content': json.dumps({
            'mode': mode, 'actions': config['task']['evaluation_criteria']['actions'],
        })})
        if mode == 'budget':
            setup['tool_relay']['max_calls'] = 1
        result = run_one(harness, dataset.control_task(tid), setup=setup,
                         verifier=dataset.VERIFY[tid], env=dataset.ENV[tid],
                         run_id='tau3-request-errors', trial_id=mode,
                         run_seed=str(dataset.TRIAL_PROTOCOL['seeds'][0]),
                         recordings_root=tmp_path / 'recordings', sandbox=True)

    relay = result['runtime_services']['tool_relay']
    assert relay['cleanup']['complete'], result
    transport = relay['receipt']['transport']
    if mode in ('provider', 'budget'):
        assert result['invalid']['stage'] == 'tool relay', result
        assert relay['faults'] and relay['recording'] is None
        assert relay['receipt']['failure'] == ('ToolTransportError' if mode == 'provider' else 'transport_failure')
        assert transport['request_errors'] == 4
        if mode == 'provider':
            assert transport['errors'] == 5
            assert relay['model']['faults']
        else:
            assert transport['errors'] == 4 and transport['refused'] == 1
        return

    assert 'invalid' not in result and result['exit_code'] == 0, result
    assert not relay['faults']
    assert relay['receipt']['failure'] is None
    assert transport['errors'] == transport['request_errors'] == 8
    sealed = json.loads(Path(relay['recording']['archive']).read_text())
    assert sealed['binding']['trial_id'] == mode
    assert sealed['payload']['failure'] is None
    assert sealed['payload']['num_errors'] == (10 if mode == 'official_limit' else 0)
    verdict = result['verdict']
    if mode == 'incomplete':
        assert sealed['payload']['termination_reason'] is None
        assert verdict['score'] is None and verdict['outcome'] == 'unmeasured'
        assert verdict['evidence']['error'] == 'IncompleteConversation'
    else:
        expected = {'success': 'agent_stop', 'user_stop': 'user_stop', 'official_limit': 'too_many_errors'}[mode]
        assert sealed['payload']['termination_reason'] == expected
        assert verdict['evidence']['measured'] is True
        assert verdict['score'] == float(mode == 'success')
