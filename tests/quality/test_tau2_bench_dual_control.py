"""Genuine dual-control regressions, distinct from the assistant-only three-task control.

The local completion fixture is explicit, not an LLM judge. Payload-backed tests run
UserSimulator, the private relay, trusted sealing and EvaluationType.ALL. Sandbox
execution is also available via control.py --dual --platform (not claimed by these
host-side integration tests). No real model endpoints are used.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data.datasets import tau2_bench as dataset

spec = importlib.util.spec_from_file_location('tau2_dual_control', dataset.ASSETS / 'control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def payload_present():
    try:
        return (dataset.DATASET_ROOT / 'manifest.json').is_file()
    except Exception:
        return False


payload = pytest.mark.skipif(not payload_present(), reason='no prepared tau2 storage')
runtime = pytest.mark.skipif(sys.version_info[:2] != (3, 13), reason='official tau2 requires Python 3.13')


def test_explicit_fixture_tool_response_and_continuation():
    body = {'model': 'tau2-control-user', 'messages': []}
    message = control.fixture_message(body, dual=True)
    assert message['content'] is None
    assert message['tool_calls'] == [{'id': control.DUAL_CALL_ID, 'type': 'function',
                                     'function': {'name': 'toggle_data', 'arguments': '{}'}}]
    body['messages'] = [{'role': 'tool', 'tool_call_id': control.DUAL_CALL_ID,
                         'content': 'Mobile Data is now ON.\nStatus Bar: test'}]
    assert control.fixture_message(body, dual=True)['content'] == control.DUAL_CONTINUATION
    body['messages'][0]['content'] = 'Mobile Data is now OFF.'
    with pytest.raises(ValueError, match='real successful'):
        control.fixture_message(body, dual=True)


def test_endpoint_restores_environment_even_on_failure(monkeypatch):
    keys = ['HG_ENV_MODEL', 'HG_ENV_BASE_URL', 'HG_ENV_API_KEY', 'HG_AGENT_BACKEND',
            'PATH', 'HG_JUDGE_MODEL', 'HG_JUDGE_BASE_URL', 'HG_JUDGE_API_KEY']
    monkeypatch.setenv('HG_ENV_MODEL', 'original')
    monkeypatch.delenv('HG_JUDGE_MODEL', raising=False)
    before = {k: os.environ.get(k) for k in keys}
    with pytest.raises(RuntimeError, match='control failure'):
        with control.Endpoint(dual=True):
            assert os.environ['HG_ENV_MODEL'] == 'tau2-control-user'
            raise RuntimeError('control failure')
    assert {k: os.environ.get(k) for k in keys} == before


def _record():
    return {'schema': 'harnessgrad.interaction-record.v1',
            'binding': {'task_id': control.DUAL_TASK},
            'payload': {'failure': None, 'num_errors': 0, 'messages': [
                {'role': 'user', 'tool_calls': [{'id': control.DUAL_CALL_ID,
                    'name': 'toggle_data', 'arguments': {}, 'requestor': 'user'}]},
                {'role': 'tool', 'id': control.DUAL_CALL_ID, 'requestor': 'user',
                 'error': False, 'content': 'Mobile Data is now ON.\nStatus Bar: test'},
                {'role': 'user', 'content': control.DUAL_CONTINUATION}]}}


@pytest.mark.parametrize('mutation', ['assistant_call', 'assistant_result', 'wrong_result',
                                      'missing_continuation', 'unbound', 'runtime_error'])
def test_episode_assertions_reject_proxy_or_broken_user_path(mutation):
    record = _record()
    messages = record['payload']['messages']
    if mutation == 'assistant_call':
        messages[0]['tool_calls'][0]['requestor'] = 'assistant'
    elif mutation == 'assistant_result':
        messages[1]['requestor'] = 'assistant'
    elif mutation == 'wrong_result':
        messages[1]['content'] = 'Mobile Data is now OFF.'
    elif mutation == 'missing_continuation':
        messages[2]['content'] = 'not the fixture continuation'
    elif mutation == 'unbound':
        record['binding']['task_id'] = 'wrong-task'
    else:
        record['payload']['num_errors'] = 1
    with pytest.raises(ValueError):
        control.assert_dual_episode(record, control.DUAL_TASK)


@payload
def test_selected_task_has_both_real_requestors_and_no_judge_dependency():
    config, assistant_actions = control._gold_actions(control.DUAL_TASK, dual=True)
    criteria = config['task']['evaluation_criteria']
    assert criteria['reward_basis'] == ['ENV_ASSERTION']
    assert not criteria.get('nl_assertions')
    assert not criteria.get('communicate_info')
    assert [(a['name'], a['requestor']) for a in criteria['actions']] == [
        ('toggle_data', 'user'), ('refuel_data', 'assistant')]
    assert [a['name'] for a in assistant_actions] == ['refuel_data']
    assert {a['env_type'] for a in criteria['env_assertions']} == {'user', 'assistant'}
    with pytest.raises(ValueError, match='assistant-only'):
        control._gold_actions(control.DUAL_TASK)


@payload
@runtime
@pytest.mark.parametrize('oracle,expected', [(True, 1.0), (False, 0.0)])
def test_official_simulator_user_tool_sealed_episode_and_all_scorer(tmp_path, oracle, expected):
    with control.Endpoint(dual=True) as endpoint:
        score, detail = control.run(oracle, False, tmp_path, control.DUAL_TASK, dual=True)
    assert detail['returncode'] == 0, detail
    assert score == expected, detail
    official = detail['evidence']['official']
    env_checks = {c['env_assertion']['func_name']: c['met'] for c in official['env_assertions']}
    # Official status means working connectivity, not merely the enabled switch:
    # without refueling the USER toggle succeeds but all final assertions fail.
    assert env_checks == {'assert_mobile_data_status': oracle,
                          'assert_internet_speed': oracle,
                          'assert_data_refueling_amount': oracle}
    action_checks = {c['action']['requestor']: c['action_match'] for c in official['action_checks']}
    assert action_checks == {'user': True, 'assistant': oracle}
    assert detail['dual_assertions'] == {'user_tool': 'toggle_data', 'requestor': 'user',
        'response_checked': True, 'continuation_checked': True, 'trusted_binding_checked': True}
    # One completion emits the tool call, one consumes its result and continues.
    # The assistant STOP marker is terminal; it does not request a third completion.
    assert len(endpoint.requests) == 2
    assert not endpoint.errors
    assert all(r['model'] == 'tau2-control-user' for r in endpoint.requests)
    archives = list((tmp_path / 'recordings').glob('interaction-*/episode.json'))
    assert len(archives) == 1
    record = json.loads(archives[0].read_text())
    assert record['payload']['revision'] == 'v0.1.0'
    assert record['payload']['case_task_id'] == control.DUAL_TASK


@pytest.mark.parametrize('previous', [None, '/tmp/original-control-root'])
@payload
def test_platform_restores_work_root_on_runner_error(monkeypatch, tmp_path, previous):
    if previous is None:
        monkeypatch.delenv('HARNESSGRAD_WORK_ROOT', raising=False)
    else:
        monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', previous)

    def fail(**kwargs):
        assert kwargs['sandbox'] is True
        assert os.environ['HARNESSGRAD_WORK_ROOT'] == str(tmp_path)
        raise RuntimeError('runner failed')

    monkeypatch.setattr(control, 'run_one', fail)
    with control.Endpoint(dual=True):
        with pytest.raises(RuntimeError, match='runner failed'):
            control.run(True, True, tmp_path, control.DUAL_TASK, dual=True)
    assert os.environ.get('HARNESSGRAD_WORK_ROOT') == previous


def test_control_checks_start_and_terminal_protocol_errors(monkeypatch):
    monkeypatch.setattr(control, 'call', lambda *args, **kwargs: {'error': 'conversation_ended'})
    for operation in ('start', 'call', 'say'):
        with pytest.raises(RuntimeError, match=f'{operation}: conversation_ended'):
            control.checked_call('unused', operation, {})


def test_main_counts_requests_per_task_not_cumulatively(monkeypatch, tmp_path, capsys):
    def fake_run(oracle, platform, work, tid, dual=False):
        # Exercise main's accounting without storage or any network call.
        endpoint.requests.extend([{'model': 'tau2-control-user'}] * (1 if tid == 'one' else 2))
        return 1.0, {}
    endpoint = control.Endpoint()
    monkeypatch.setattr(control, 'Endpoint', lambda **kwargs: endpoint)
    monkeypatch.setattr(control, 'run', fake_run)
    monkeypatch.setattr(sys, 'argv', ['control.py', '--oracle', '--task-id', 'one', '--task-id', 'two'])
    assert control.main() == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert [t['model_calls'] for t in result['tasks']] == [1, 2]
    assert [t['environment_calls'] for t in result['tasks']] == [1, 2]


def test_retained_archive_references_survive_temporary_work_cleanup(tmp_path):
    source = tmp_path / 'temporary/recordings'
    target = tmp_path / 'evidence/recordings'
    target.mkdir(parents=True)
    (target / 'episode.json').write_text('{"sealed": true}')
    original = str(source / 'episode.json')
    saved = control.retain_recordings(
        {'verdict': {'interaction_record': {'archive': original, 'sha256': 'unchanged'}}},
        source, target)
    record = saved['verdict']['interaction_record']
    assert Path(record['archive']).is_file()
    assert record['original_archive'] == original
    assert record['sha256'] == 'unchanged'
    assert not source.exists()


def test_retention_refuses_missing_copied_archive(tmp_path):
    with pytest.raises(ValueError, match='retained recording is missing'):
        control.retain_recordings({'archive': str(tmp_path / 'source/missing.json')},
                                  tmp_path / 'source', tmp_path / 'target')
