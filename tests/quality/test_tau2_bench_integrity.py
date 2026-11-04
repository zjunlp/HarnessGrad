"""Offline private identity, health and actual v0.1.0 judge coverage regressions."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / 'data/datasets/tau2_bench/lib'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scoring = load('tau2_integrity_scoring', LIB / 'scoring.py')


def fixtures():
    config = {'release': 'v0.1.0', 'domain': 'airline', 'task_id': 'tau2_bench/airline/fixture',
              'case_sha256': 'a' * 64, 'seed': 7, 'task': {'id': 'fixture'}}
    episode = {'revision': 'v0.1.0', 'domain': 'airline', 'task_id': 'fixture',
               'case_task_id': config['task_id'], 'case_sha256': config['case_sha256'],
               'seed': 7, 'failure': None, 'termination_reason': 'user_stop', 'messages': []}
    return config, episode


def test_valid_identity_needs_no_dataset_storage():
    scoring.validate_identity(*fixtures())


@pytest.mark.parametrize('key,value', [('domain', 'retail'), ('task_id', 'other'),
    ('revision', 'v1.0.1'), ('case_task_id', 'tau2_bench/retail/fixture'),
    ('case_sha256', 'b' * 64)])
def test_private_identity_is_not_derived_from_episode(key, value):
    config, episode = fixtures()
    episode[key] = value
    with pytest.raises(ValueError, match='identity mismatch'):
        scoring.validate_identity(config, episode)


@pytest.mark.parametrize('key', ['failure', 'termination_reason', 'messages', 'seed'])
def test_missing_health_evidence_rejected(key):
    config, episode = fixtures()
    del episode[key]
    with pytest.raises((ValueError, KeyError)):
        scoring.validate_identity(config, episode)


@pytest.mark.parametrize('failure', ['TimeoutError', 'service_deadline', 'transport_failure', '', False, {}])
def test_non_null_runtime_failure_never_becomes_a_score(failure):
    config, episode = fixtures()
    episode['failure'] = failure
    with pytest.raises(RuntimeError, match='private runtime failed'):
        scoring.validate_identity(config, episode)


def test_unfinished_episode_cannot_invent_agent_error():
    config, episode = fixtures()
    episode['termination_reason'] = None
    with pytest.raises(ValueError, match='unfinished_episode_without_official_termination'):
        scoring.score(config, episode)


@pytest.mark.parametrize('messages,seed', [(None, 7), ({}, 7), ([], True), ([], '7')])
def test_corrupt_episode_shape_rejected(messages, seed):
    config, episode = fixtures()
    episode.update(messages=messages, seed=seed)
    with pytest.raises(ValueError):
        scoring.validate_identity(config, episode)


def test_relay_snapshots_trusted_identity_and_rejects_state_corruption():
    # Relay import is host-only: importing Service never bootstraps tau2/model.
    relay = load('tau2_integrity_relay', ROOT / 'data/datasets/tau2_bench/relay.py')
    config, episode = fixtures()
    class State:
        def episode(self):
            return deepcopy(episode)
    service = relay.Service(config, State())
    config['domain'] = 'retail'
    assert service.episode()['domain'] == 'airline'
    episode['domain'] = 'retail'
    with pytest.raises(ValueError, match='identity mismatch'):
        service.episode()


def payload(resolve=None):
    """Storage resolution can raise (unconfigured mount); that is an explicit skip."""
    try:
        if resolve is None:
            sys.path.insert(0, str(ROOT))
            from data.datasets import tau2_bench
            runtime, root = tau2_bench.RUNTIME, tau2_bench.DATASET_ROOT
        else:
            runtime, root = resolve()
        python = runtime / 'bin/python'
        bundle = root / 'bundle'
        if not python.is_file() or not (bundle / 'manifest.json').is_file():
            return None, 'tau2 pinned runtime/bundle unavailable'
        return (python, bundle), None
    except Exception:
        return None, 'tau2 dataset storage unavailable'


def test_storage_unavailable_is_safe():
    def unavailable():
        raise RuntimeError('unconfigured storage')
    assert payload(unavailable) == (None, 'tau2 dataset storage unavailable')
    assert payload(lambda: (Path('/definitely/missing'), Path('/definitely/missing')))[0] is None


# Run under the release's actual Python, never import >=3.13 code into host 3.12.
JUDGE_PROBE = r'''
import json, os, socket, sys
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from bootstrap import bootstrap
bootstrap(Path(sys.argv[2]))
# Prevent accidental providers or external network from becoming part of a fixture.
def offline(*a, **kw):
    raise AssertionError('network forbidden in deterministic fixture')
socket.socket.connect = offline
from scoring import score
from model import install
from tau2.data_model.tasks import Task
from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.evaluator.evaluator import evaluate_simulation, EvaluationType
from tau2.utils.utils import get_now
os.environ['HG_JUDGE_SOCKET'] = '/unused-deterministic-fixture'
config = {'release': 'v0.1.0', 'domain': 'airline', 'task_id': 'tau2_bench/airline/fixture',
          'case_sha256': 'a'*64, 'task': {'id': 'fixture',
          'user_scenario': {'instructions': 'Deterministic fixture only.'},
          'evaluation_criteria': {'nl_assertions': ['first', 'second'], 'reward_basis': ['DB']}}}
episode = {'revision': 'v0.1.0', 'domain': 'airline', 'task_id': 'fixture',
           'case_task_id': config['task_id'], 'case_sha256': config['case_sha256'],
           'seed': 7, 'failure': None, 'termination_reason': 'user_stop', 'messages': []}
row = lambda assertion, met=True: {'expectedOutcome': assertion, 'metExpectation': met, 'reasoning': 'fixture'}
calls = []
def use(content, tool_calls=None):
    def transport(*args, **kwargs):
        calls.append(args)
        return {'message': {'content': content, 'tool_calls': tool_calls},
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}
    install('judge', transport)
# Official evaluator called unchanged: the NL result is performed but ignored by DB basis.
use(json.dumps({'results': [row('first', False), row('second')]}))
result = score(config, episode)
assert result['reward'] == 1.0, result
assert result['judge_coverage'] == {'status': 'performed', 'expected_count': 2,
    'observed_count': 2, 'used_in_reward': False, 'complete': True}, result
assert len(calls) == 1
# Compare the wrapper's reward to direct official ALL over precisely the same fixture.
now = get_now()
run = SimulationRun(id='direct', task_id='fixture', start_time=now, end_time=now,
    duration=0, termination_reason=TerminationReason.USER_STOP, messages=[], seed=7)
assert evaluate_simulation(run, Task.model_validate(config['task']), EvaluationType.ALL,
    solo_mode=False, domain='airline').reward == result['reward']
config['task']['evaluation_criteria']['reward_basis'] = ['NL_ASSERTION']
assert score(config, episode)['reward'] == 0.0
config['task']['evaluation_criteria']['reward_basis'] = ['DB']
rejected = []
for label, content in [
    ('missing', '{}'), ('empty', '{"results": []}'),
    ('partial', json.dumps({'results': [row('first')]})),
    ('duplicate', json.dumps({'results': [row('first'), row('first')]})),
    ('extra', json.dumps({'results': [row('first'), row('second'), row('extra')]})),
    ('unexpected', json.dumps({'results': [row('first'), row('other')]})),
    ('coerced_bool', json.dumps({'results': [row('first', 'true'), row('second')]})),
    ('null', 'null'), ('invalid_json', '{'),
    ('duplicate_key', '{"results": [], "results": []}'),
    ('bad_reasoning', json.dumps({'results': [{'expectedOutcome': 'first', 'metExpectation': True}]})),
]:
    use(content)
    try:
        score(config, episode)
    except (ValueError, TypeError, KeyError):
        rejected.append(label)
    else:
        raise AssertionError('accepted damaged judge output: ' + label)
# The same model bridge must retain batching for the real released UserSimulator.
from tau2.user.user_simulator import UserSimulator
from tau2.data_model.message import AssistantMessage
os.environ['HG_ENV_MODEL_SOCKET'] = '/unused-deterministic-fixture'
def environment_transport(*args, **kwargs):
    return {'message': {'content': None, 'tool_calls': [
        {'id': 'one', 'function': {'name': 'first_tool', 'arguments': '{}'}},
        {'id': 'two', 'function': {'name': 'second_tool', 'arguments': '{}'}}]},
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}
install('environment', environment_transport)
simulator = UserSimulator(llm='unused-fixture', instructions='Deterministic fixture.', tools=None)
user_message, _ = simulator.generate_next_message(AssistantMessage(role='assistant', content='hello'), simulator.get_init_state())
assert [call.name for call in user_message.tool_calls] == ['first_tool', 'second_tool']
assert all(call.requestor == 'user' for call in user_message.tool_calls)
use('{"results": []}', tool_calls=[{'unexpected': 'judge tool call'}])
try:
    score(config, episode)
except ValueError as exc:
    assert str(exc) == 'malformed_nl_judge_message'
else:
    raise AssertionError('judge tool-call message accepted')
# Upstream early termination returns its own zero without calling the judge.
for termination in ('max_steps', 'too_many_errors'):
    calls.clear()
    episode['termination_reason'] = termination
    result = score(config, episode)
    assert result['reward'] == 0.0 and not calls, result
    assert result['judge_coverage']['status'] == 'skipped_official_early_termination'
    assert result['judge_coverage']['complete'] is False
assert 'agent_error' not in [reason.value for reason in TerminationReason]
print(json.dumps({'rejected': rejected, 'actual_release': 'v0.1.0', 'early_termination_reward': 0.0}))
'''


def test_actual_release_all_judge_performed_vs_reward_basis_and_corruption():
    available, reason = payload()
    if reason:
        pytest.skip(reason)
    python, bundle = available
    proc = subprocess.run([str(python), '-I', '-B', '-c', JUDGE_PROBE, str(LIB), str(bundle)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-6000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result['actual_release'] == 'v0.1.0'
    assert len(result['rejected']) == 11


@pytest.mark.parametrize('field,value', [('domain', 'retail'), ('revision', 'v1.0.1'),
    ('task_id', 'another-release-id'), ('case_task_id', 'another-platform-id'),
    ('case_sha256', 'b' * 64), ('seed', 8)])
def test_verifier_rejects_private_mismatch_before_loading_runtime(tmp_path, field, value):
    import hashlib
    config, episode = fixtures()
    episode[field] = value
    raw = json.dumps(config).encode()
    digest = hashlib.sha256(raw).hexdigest()
    # The sealed record binds the actual file digest, not our synthetic placeholder.
    if field != 'case_sha256':
        episode['case_sha256'] = digest
    (tmp_path / 'tau2_private.json').write_bytes(raw)
    trusted = tmp_path / 'trusted_episode'
    trusted.mkdir()
    (trusted / 'episode.json').write_text(json.dumps({'schema': 'harnessgrad.interaction-record.v1',
        'binding': {'task_id': config['task_id'], 'seed': 7}, 'payload': episode}))
    proc = subprocess.run([sys.executable, str(ROOT / 'data/datasets/tau2_bench/verify/check.py'),
        '--task-id', config['task_id'], '--case-sha256', digest], cwd=tmp_path,
        capture_output=True, text=True, timeout=20)
    assert proc.returncode == 3, proc.stderr
    evidence = json.loads((tmp_path / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is False
    assert 'identity mismatch' in evidence['detail']
    assert not (tmp_path / 'verifier/reward.txt').exists()


@pytest.mark.parametrize('operation,arguments', [
    ('start', {}), ('say', {'message': 'hello'}),
    ('call', {'tool_name': 'lookup', 'arguments': {}}), ('status', {})])
def test_service_first_failure_is_sticky_and_sealed(operation, arguments):
    relay = load('tau2_integrity_relay_failure', ROOT / 'data/datasets/tau2_bench/relay.py')
    config, episode = fixtures()
    class State:
        termination_reason = None
        bootstrap_complete = True
        exception = TimeoutError
        def fail(self, **kw):
            raise self.exception('deterministic fixture')
        start_conversation = submit_assistant_message = call_assistant_tool = get_runtime_status = fail
        def episode(self):
            return deepcopy(episode)
    state = State()
    service = relay.Service(config, state)
    with pytest.raises(TimeoutError):
        service.handle(operation, arguments)
    state.exception = ValueError
    with pytest.raises(ValueError):
        service.handle(operation, arguments)
    assert service.failure == 'TimeoutError'
    assert service.episode()['failure'] == 'TimeoutError'
    with pytest.raises(RuntimeError, match='private runtime failed'):
        scoring.validate_identity(config, service.episode())


def test_protocol_refusals_do_not_poison_healthy_episode():
    relay = load('tau2_integrity_relay_protocol', ROOT / 'data/datasets/tau2_bench/relay.py')
    config, episode = fixtures()
    class State:
        termination_reason = None
        bootstrap_complete = False
        def fail(self, **kw):
            raise AssertionError('invalid request reached runtime')
        start_conversation = submit_assistant_message = call_assistant_tool = get_runtime_status = fail
        def episode(self):
            return deepcopy(episode)
    service = relay.Service(config, State())
    for operation, arguments in [('reset', {}), ('start', {'seed': 1}), ('say', []),
            ('say', {'message': ''}), ('call', {'tool_name': 42, 'arguments': {}}),
            ('say', {'message': 'before start'})]:
        assert 'error' in service.handle(operation, arguments)
        assert service.failure is None
    service.state.termination_reason = 'max_steps'
    assert service.handle('start', {})['error'] == 'conversation_ended'
    assert service.failure is None


def test_duplicate_sealed_json_keys_and_stale_rewards_are_rejected(tmp_path):
    import hashlib
    config, _ = fixtures()
    raw = json.dumps(config).encode()
    (tmp_path / 'tau2_private.json').write_bytes(raw)
    trusted = tmp_path / 'trusted_episode'
    trusted.mkdir()
    (trusted / 'episode.json').write_text('{"schema": "bad", "schema": "harnessgrad.interaction-record.v1"}')
    output = tmp_path / 'verifier'
    output.mkdir()
    (output / 'reward.txt').write_text('1.0\n')
    proc = subprocess.run([sys.executable, str(ROOT / 'data/datasets/tau2_bench/verify/check.py'),
        '--task-id', config['task_id'], '--case-sha256', hashlib.sha256(raw).hexdigest()],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 3
    evidence = json.loads((output / 'evidence.json').read_text())
    assert evidence['measured'] is False and 'duplicate trusted JSON key' in evidence['detail']
    assert not (output / 'reward.txt').exists()


@pytest.mark.parametrize('arm,expected_reward', [('marker', 1.0), ('empty', 0.0), ('budget', 0.0)])
def test_actual_verifier_uses_official_communicate_and_early_termination(tmp_path, arm, expected_reward):
    """Synthetic fixture only: real pin's COMMUNICATE criterion, not a benchmark proxy."""
    import hashlib
    available, reason = payload()
    if reason:
        pytest.skip(reason)
    python, bundle = available
    config, episode = fixtures()
    config['task'].update(user_scenario={'instructions': 'Deterministic fixture only.'},
        evaluation_criteria={'communicate_info': ['fixture marker'], 'reward_basis': ['COMMUNICATE']})
    if arm == 'marker':
        episode['messages'] = [{'role': 'assistant', 'content': 'fixture marker'}]
    if arm == 'budget':
        episode['termination_reason'] = 'max_steps'
    raw = json.dumps(config).encode()
    digest = hashlib.sha256(raw).hexdigest()
    episode['case_sha256'] = digest
    (tmp_path / 'tau2_private.json').write_bytes(raw)
    trusted = tmp_path / 'trusted_episode'
    trusted.mkdir()
    (trusted / 'episode.json').write_text(json.dumps({'schema': 'harnessgrad.interaction-record.v1',
        'binding': {'task_id': config['task_id'], 'seed': 7}, 'payload': episode}))
    (tmp_path / 'tau2_bundle').symlink_to(bundle, target_is_directory=True)
    transport = tmp_path / 'tau2_transport_runtime'
    transport.mkdir()
    (transport / 'tool_transport.py').write_text('def call(*args, **kwargs):\n    raise AssertionError("unexpected model call in deterministic fixture")\n')
    proc = subprocess.run([str(python), '-I', '-B', str(ROOT / 'data/datasets/tau2_bench/verify/check.py'),
        '--task-id', config['task_id'], '--case-sha256', digest], cwd=tmp_path,
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-6000:]
    output = tmp_path / 'verifier'
    evidence = json.loads((output / 'evidence.json').read_text())
    assert evidence['measured'] is True and evidence['revision'] == 'v0.1.0'
    assert evidence['official']['reward'] == expected_reward
    assert float((output / 'reward.txt').read_text()) == expected_reward
    assert evidence['scorer_input']['verified'] is True
    assert evidence['judge_coverage']['status'] == ('skipped_official_early_termination' if arm == 'budget' else 'not_required')
