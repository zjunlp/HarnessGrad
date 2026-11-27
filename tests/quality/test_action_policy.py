"""Exercise composition and isolation without loading a model or simulator."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import subprocess
from threading import Barrier, Event
import time
from types import SimpleNamespace

import pytest

from data.action_policy_ports.robocasa365 import environment
from eval.service import action_policy
from eval.service.action_policy import bind_setups, EpisodePolicy, PROTOCOL
from eval.service.tool_transport import Relay, ToolTransportError, call
from harnessgrad.provenance import verification_context


def fixture_service(path, *, control=None, events=None, fail=False, value=0.0, before_predict=None,
                    checkpoint='same', policy_handler=None, **description_fields):
    env = environment()
    identity = {'protocol': PROTOCOL, 'control': control or env['control'],
                'cameras': env['cameras'], 'states': env['states'],
                'actions': list(env['actions']), 'chunk_size': 2, 'checkpoint': checkpoint,
                **description_fields}

    def handle(op, args):
        if op == 'describe':
            return identity
        if policy_handler is not None:
            return policy_handler(op, args)
        if fail:
            raise RuntimeError('private service detail')
        assert op == 'predict'
        if before_predict is not None:
            before_predict()
        events.append(args)
        return {'actions': [{name: [value] * size for name, size in env['actions'].items()}] * 2}

    return Relay(path, handle)


def bind(socket, tasks):
    setups = {task: {'tool_relay': {'program': 'data/datasets/robocasa365/relay.py'}, 'files': []}
              for task in tasks}
    envs = {task: {'kind': 'files', 'network': 'none'} for task in tasks}
    return bind_setups(socket, 'robocasa365', tasks, setups, envs, sandbox=True)


def episode(path, *, horizon=10, before_step=None):
    actions_taken = []

    def public_observation():
        step = len(actions_taken)
        return {'step': step, 'horizon': horizon, 'ended': step >= horizon,
                'images': {'left': str(step)}, 'state': {'position': [step]}, 'instruction': 'Original goal.'}

    def handle(op, args):
        if op == 'step_many':
            if before_step is not None:
                before_step()
            actions_taken.extend(args['actions'])
        else:
            assert op == 'observe'
        return public_observation()

    return Relay(path, handle)


def test_delegation_reobserves_and_keeps_episodes_separate(tmp_path):
    seen = []
    with fixture_service(tmp_path / 'predict.sock', events=seen) as model:
        bound = bind(model.path, ['a', 'b'])
        assert 'action_policy_client.py' in bound['a']['runtime_prompt']
        assert bound['a']['files'][-1]['path'] == 'action_policy_client.py'
        with episode(tmp_path / 'a.sock', horizon=3) as first, episode(tmp_path / 'b.sock') as second:
            routes = []
            for task, env in [('a', first), ('b', second)]:
                routes.append(EpisodePolicy(bound[task], env.path, task_id=task,
                                            trial_id='1', run_seed='1', timeout_s=20).start())
            try:
                assert call(routes[0].path, 'observe')['step'] == 0
                invalid = call(routes[0].path, 'policy.run', {'instruction': 'a', 'steps': True})
                assert 'error' in invalid
                with ThreadPoolExecutor(max_workers=2) as threads:
                    results = list(threads.map(lambda pair: call(pair[1].path, 'policy.run',
                        {'instruction': pair[0], 'steps': 5}), zip(['a', 'b'], routes)))
                assert [result['executed_steps'] for result in results] == [3, 5]
                assert results[0]['ended'] and results[0]['images']['left'] == '3'
                assert not results[1]['ended'] and results[1]['state']['position'] == [5]
                assert [r['state']['position'][0] for r in seen if r['instruction'] == 'a'] == [0, 2]
                assert [r['state']['position'][0] for r in seen if r['instruction'] == 'b'] == [0, 2, 4]
                assert len({r['seed'] for r in seen}) == 5
                assert all(set(request) == {'identity', 'instruction', 'images', 'state', 'seed'}
                           for request in seen)
            finally:
                for route in routes:
                    route.close()


def test_parallel_episodes_queue_predictions_and_overlap_environment_work(tmp_path, monkeypatch):
    tasks = [f'task-{index}' for index in range(10)]
    observed = Barrier(len(tasks), timeout=10)
    actions_ready = Barrier(len(tasks), timeout=10)

    def environment_call(op, args):
        if op == 'observe':
            observed.wait()
            return {'step': 0, 'horizon': 1, 'ended': False, 'images': {}, 'state': {}}
        assert op == 'step_many'
        actions_ready.wait()
        return {'step': 1, 'horizon': 1, 'ended': True}

    with ExitStack() as resources:
        model = resources.enter_context(fixture_service(
            tmp_path / 'policy.sock', events=[], before_predict=lambda: time.sleep(0.05)))
        setups = bind(model.path, tasks)
        routes = []
        for task in tasks:
            route = EpisodePolicy(setups[task], None, task_id=task, trial_id='1',
                                  run_seed='1', timeout_s=20)
            resources.callback(route.close)
            monkeypatch.setattr(route, '_environment', environment_call)
            routes.append(route)
        with ThreadPoolExecutor(max_workers=len(routes)) as pool:
            results = list(pool.map(lambda route: route.handle(
                'policy.run', {'instruction': 'move', 'steps': 1}), routes))

        assert [result.get('executed_steps') for result in results] == [1] * len(routes)
        assert all(route.fault is None for route in routes)
        assert max(route.summary['queue_seconds'] for route in routes) > 0.05


def test_waiting_prediction_expires_without_blocking_another_socket(tmp_path):
    predicting = Event()
    release = Event()
    queued_predictions = []
    args = {'instruction': 'move', 'steps': 1}

    def hold_prediction():
        predicting.set()
        assert release.wait(10)

    with ExitStack() as resources:
        busy = resources.enter_context(fixture_service(
            tmp_path / 'busy.sock', events=queued_predictions, before_predict=hold_prediction))
        idle = resources.enter_context(fixture_service(tmp_path / 'idle.sock', events=[]))
        busy_setups = bind(busy.path, ['first', 'waiting'])
        idle_setup = bind(idle.path, ['independent'])['independent']

        def route_for(task, setup, timeout):
            relay = resources.enter_context(episode(tmp_path / f'{task}.sock'))
            route = EpisodePolicy(setup, relay.path, task_id=task, trial_id='1',
                                  run_seed='1', timeout_s=timeout)
            resources.callback(route.close)
            return route

        first = route_for('first', busy_setups['first'], 15)
        independent = route_for('independent', idle_setup, 10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(first.handle, 'policy.run', args)
            try:
                assert predicting.wait(5)
                assert independent.handle('policy.run', args)['executed_steps'] == 1
                waiting = route_for('waiting', busy_setups['waiting'], 0.25)
                assert waiting.handle('policy.run', args) == {
                    'error': 'action policy could not complete the request'}
                assert waiting.summary['queue_seconds'] >= 0.1
                assert waiting.summary['queue_timeouts'] == 1
                assert waiting.summary['predictions'] == 0
                assert waiting.fault is None
            finally:
                release.set()
            assert active.result(timeout=5)['executed_steps'] == 1
        assert len(queued_predictions) == 1


def test_policy_failure_and_contract_mismatch(tmp_path):
    with fixture_service(tmp_path / 'mismatch.sock', control='other') as model:
        with pytest.raises(ValueError, match='incompatible control'):
            bind(model.path, ['task'])
    with fixture_service(tmp_path / 'fail.sock', fail=True) as model, episode(tmp_path / 'env.sock') as env:
        setup = bind(model.path, ['task'])['task']
        route = EpisodePolicy(setup, env.path, task_id='task', trial_id='1', run_seed='1', timeout_s=20).start()
        try:
            rejected = call(route.path, 'policy.run', {'instruction': 'move', 'steps': 2})
            assert rejected == {'error': 'action policy could not complete the request'}
            assert route.fault == 'predict: ToolTransportError'
            assert route.summary['policy_seconds'] > 0
            assert call(env.path, 'observe')['step'] == 0
        finally:
            route.close()


@pytest.mark.parametrize(('description_fields', 'expected'), [({}, 60), ({'predict_timeout_s': 10}, 10)])
def test_prediction_deadline_uses_service_description(tmp_path, monkeypatch, description_fields, expected):
    with fixture_service(tmp_path / 'model.sock', **description_fields) as service:
        setup = bind(service.path, ['task'])['task']
    monkeypatch.setattr(action_policy, 'time', SimpleNamespace(monotonic=lambda: 100))
    monkeypatch.setattr(action_policy, 'call', lambda *args, deadline, **kwargs: deadline)
    route = EpisodePolicy(setup, None, task_id='task', trial_id='1', run_seed='1', timeout_s=120)
    try:
        assert route._predict({}) == 100 + expected
    finally:
        route.close()


@pytest.mark.parametrize('timeout', [None, True, '10', 0, -1, float('inf'), float('nan'), 10**310])
def test_policy_rejects_invalid_prediction_timeout(tmp_path, timeout):
    with fixture_service(tmp_path / 'model.sock', predict_timeout_s=timeout) as service:
        with pytest.raises(ValueError, match='predict_timeout_s must be a positive finite number'):
            bind(service.path, ['task'])


@pytest.mark.parametrize('expiry', ['prediction', 'episode', 'close'])
def test_dispatched_prediction_timeout_and_close_release_queue(tmp_path, expiry):
    started, release = Event(), Event()

    def hold():
        started.set()
        assert release.wait(5)

    prediction_timeout = 0.25 if expiry == 'prediction' else 10
    args = {'instruction': 'move', 'steps': 1}
    with fixture_service(tmp_path / 'model.sock', events=[], before_predict=hold,
                         predict_timeout_s=prediction_timeout) as service, \
            episode(tmp_path / 'env.sock') as env, ThreadPoolExecutor(max_workers=1) as pool:
        setup = bind(service.path, ['task'])['task']
        route = EpisodePolicy(setup, env.path, task_id='task', trial_id='1', run_seed='1',
                              timeout_s=0.5 if expiry == 'episode' else 20).start()
        pending = pool.submit(call, route.path, 'policy.run', args, timeout_s=2)
        try:
            assert started.wait(2)
            if expiry == 'close':
                route.close()
            assert pending.result(timeout=2) == {'error': 'action policy could not complete the request'}
            assert not route.prediction_queue.tickets
            assert route.fault == ('predict: timeout' if expiry == 'prediction' else None)
            assert route.summary['policy_seconds'] > 0
            assert route.summary['executed_steps'] == 0
        finally:
            release.set()
            route.close()


@pytest.mark.parametrize('outcome', ['ready', 'episode', 'recovery'])
def test_policy_recovery_respects_both_deadlines(tmp_path, outcome):
    operations = []

    def handle(op, args):
        operations.append(op)
        if op == 'predict_ready':
            assert args == {}
            return {'ready': outcome == 'ready' and operations.count(op) > 1}
        if operations.count(op) == 1:
            return {'status': 'recovering'}
        return {'actions': [{key: [0.0] * size for key, size in environment()['actions'].items()}] * 2}

    with fixture_service(tmp_path / 'model.sock', policy_handler=handle,
                         recovery_timeout_s=0.4 if outcome == 'recovery' else 5) as model, \
            episode(tmp_path / 'env.sock', horizon=1) as env:
        setup = bind(model.path, ['task'])['task']
        route = EpisodePolicy(setup, env.path, task_id='task', trial_id='1', run_seed='1',
                              timeout_s=0.4 if outcome == 'episode' else 10)
        try:
            result = route.handle('policy.run', {'instruction': 'move', 'steps': 1})
            assert route.summary['recovery_seconds'] > 0
            if outcome == 'ready':
                assert result['executed_steps'] == 1 and route.fault is None
                assert operations == ['predict', 'predict_ready', 'predict_ready', 'predict']
            else:
                assert 'error' in result and route.summary['predictions'] == 0
                assert route.fault == ('predict_ready: timeout' if outcome == 'recovery' else None)
                assert operations.count('predict') == 1
        finally:
            route.close()


def test_actor_shutdown_at_episode_deadline_is_not_a_route_fault(tmp_path, monkeypatch):
    with fixture_service(tmp_path / 'model.sock', events=[]) as model:
        setup = bind(model.path, ['task'])['task']
        clock = SimpleNamespace(now=10)
        monkeypatch.setattr(action_policy, 'time', SimpleNamespace(monotonic=lambda: clock.now))
        route = EpisodePolicy(setup, tmp_path / 'env.sock', task_id='task', trial_id='1',
                              run_seed='1', timeout_s=20)

        def shutdown(*args, deadline, **kwargs):
            clock.now = deadline
            route.close()
            raise ToolTransportError('tool relay closed the connection mid-reply')

        monkeypatch.setattr(action_policy, 'call', shutdown)
        try:
            assert 'error' in route.handle('observe', {})
            assert route.fault is None
        finally:
            route.close()


@pytest.mark.parametrize('operation', ['policy_error', 'predict', 'step_many'])
def test_runner_scores_episode_deadlines_and_preserves_policy_faults(tmp_path, monkeypatch, operation):
    import json

    from eval.run import runner, task_session

    harness = tmp_path / 'harness'
    harness.mkdir()
    (harness / 'harness.json').write_text(json.dumps({'entrypoint': 'agent.py'}))
    (harness / 'agent.py').touch()
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    routes = []
    verified = []
    started, release = Event(), Event()
    start = task_session.TaskSession.start_action_policy
    timeout_s = 20 if operation == 'policy_error' else 1

    def hold_request():
        started.set()
        assert release.wait(5)

    def start_policy(session, *args, **kwargs):
        start(session, *args, **kwargs)
        routes.append(session.action_policy)

    def time_out(*args, **kwargs):
        try:
            reply = call(routes[0].path, 'policy.run', {'instruction': 'move', 'steps': 1}, timeout_s=3)
            assert 'error' in reply
            assert operation == 'policy_error' or started.is_set()
        finally:
            release.set()
        raise subprocess.TimeoutExpired('actor', timeout_s)

    def verify(*args, **kwargs):
        verified.append(True)
        return {'kind': 'command', 'passed': False, 'score': 0.0}

    monkeypatch.setattr(task_session.TaskSession, 'start_action_policy', start_policy)
    monkeypatch.setattr(runner.actor_execution, 'execute', time_out)
    monkeypatch.setattr(runner.task_verification, 'verify_output', verify)
    with fixture_service(tmp_path / 'model.sock', fail=operation == 'policy_error', events=[],
                         before_predict=hold_request if operation == 'predict' else None) as model, \
            episode(tmp_path / 'env.sock', before_step=hold_request if operation == 'step_many' else None) as env:
        setup = bind(model.path, ['task'])['task']
        relay = SimpleNamespace(path=env.path, fault=lambda: None, stop=lambda: None,
                                report=dict, cleanup=lambda: None, model=None, recording=None)
        monkeypatch.setattr(task_session.tool_relay, 'start', lambda *args, **kwargs: relay)
        result = runner.run_one(harness, {'task_id': 'task', 'goal': 'Move'},
                                timeout_s=timeout_s, setup=setup, env={'kind': 'files', 'network': 'none'})
    stats = result['runtime_services']['action_policy']
    if operation == 'policy_error':
        assert result['invalid']['stage'] == 'action policy route', result
        assert result.get('verdict') is None and not verified
        assert stats['fault'] == 'predict: ToolTransportError'
    else:
        assert 'invalid' not in result and verified
        assert result['verdict']['score'] == 0.0
        assert 'fault' not in stats
    timing = 'environment_seconds' if operation == 'step_many' else 'policy_seconds'
    assert stats[timing] > 0


def test_driver_preflights_and_binds_reservation_and_treatment_to_service_identity(
        tmp_path, monkeypatch, capsys):
    import json
    import sys

    import driver
    from harnessgrad.datasets import runtime_identity
    from harnessgrad.orchestration import readiness, treatment_identity

    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setattr(runtime_identity, 'h0_selection', lambda _: (
        ['task'], {'task': {'tool_relay': {'program': 'fixture.py'}}}, {},
        {'task': {'kind': 'files', 'network': 'none'}}))
    monkeypatch.setattr(readiness, 'plan_checks', lambda args: [])
    real_readiness = driver._readiness
    treatment = tmp_path / 'treatment.json'
    wrapper = tmp_path / 'wrapper.py'
    wrapper.touch()
    treatment.write_text(json.dumps({'schema': treatment_identity.SCHEMA, 'files': {'wrapper': str(wrapper)},
                                     'configuration': {}, 'environment': []}))
    bindings = []

    def record(args):
        if hasattr(args, '_action_policy_service'):
            bindings.append(treatment_identity.capture(treatment, runtime=driver._treatment_runtime(args)))
        return real_readiness(args)

    monkeypatch.setattr(driver, '_readiness', record)

    def invoke(socket, *extra):
        monkeypatch.setattr(sys, 'argv', [
            'driver.py', '--dataset', 'robocasa365', '--rounds', '0', '--sampling', 'all',
            '--run-id', 'prepared', '--runs-root', str(tmp_path / 'runs'),
            '--work-root', str(tmp_path / 'work'), '--action-policy-socket', str(socket), *extra,
        ])
        return driver.main()

    with fixture_service(tmp_path / 'original.sock') as original:
        assert invoke(original.path, '--prepare-only') == 0, capsys.readouterr()
    with fixture_service(tmp_path / 'restarted.sock') as restarted:
        assert invoke(restarted.path, '--check-only') == 0, capsys.readouterr()
    treatment_identity.require_same(*bindings, declared=True)
    prepared = tmp_path / 'runs' / 'prepared' / 'run_meta.json'
    metadata = json.loads(prepared.read_text())
    assert metadata['action_policy']['checkpoint'] == 'same'
    assert '.sock' not in prepared.read_text()

    with fixture_service(tmp_path / 'changed.sock', checkpoint='changed') as changed:
        assert invoke(changed.path, '--check-only') == 2
        assert 'preparation differs' in capsys.readouterr().err
        assert invoke(changed.path, '--check-only', '--run-id', 'other') == 0
    with pytest.raises(treatment_identity.TreatmentRefused, match='treatment changed'):
        treatment_identity.require_same(bindings[0], bindings[-1], declared=True)

    assert invoke(tmp_path / 'missing.sock', '--check-only', '--prepare-only', '--run-id', 'missing') == 2
    output = capsys.readouterr()
    assert 'action-policy' in output.out and 'action-policy' in output.err
    assert not (tmp_path / 'runs' / 'missing').exists()


@pytest.mark.parametrize('capture', [verification_context.capture, verification_context.capture_content])
def test_verification_context_binds_policy_identity_independently_of_socket(tmp_path, capture):
    task = {'task_id': 'task', 'goal': 'Move'}
    first_socket = tmp_path / 'first.sock'
    with fixture_service(first_socket) as model:
        setup = bind(model.path, ['task'])
    with fixture_service(tmp_path / 'second.sock') as model:
        relocated = bind(model.path, ['task'])
    bound = capture([task], {}, setup)
    assert bound == capture([task], {}, relocated)
    assert setup['task']['action_policy']['socket'] == str(first_socket)

    setup = setup['task']
    base = {'tool_relay': setup['tool_relay'], 'files': []}
    changed = {**setup, 'action_policy': {**setup['action_policy'], 'sha256': 'another-policy'}}
    assert bound != capture([task], {}, {'task': base})
    assert bound != capture([task], {}, {'task': changed})


def test_sandbox_delegates_through_the_only_mounted_tool_route(tmp_path):
    import base64
    import json
    import os
    from pathlib import Path
    import shutil
    import subprocess

    from eval.containers import sandbox

    if shutil.which('bwrap') is None:
        pytest.skip('bwrap is not installed')
    platform = Path(__file__).resolve().parents[2]
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGO4UyEHAAOmAXPCqsYuAAAAAElFTkSuQmCC'
    count = 0

    def env(op, args):
        nonlocal count
        if op == 'step_many':
            assert all(v == 1.0 for action in args['actions'] for values in action.values() for v in values)
            count += len(args['actions'])
        return {'step': count, 'horizon': 3, 'ended': count == 3, 'state': {}, 'images': {'camera': png}}

    with fixture_service(tmp_path / 'predict.sock', events=[], value=1.1) as model, \
            Relay(tmp_path / 'e.sock', env) as relay:
        bound = bind(model.path, ['task'])['task']
        work = tmp_path / 'work'
        task = tmp_path / 'actor-task'
        task.mkdir()
        repo = work / 'repo'
        repo.mkdir(parents=True)
        client = bound['files'][-1]
        shutil.copyfile(client['from'], task / client['path'])
        route = EpisodePolicy(bound, relay.path, task_id='task', trial_id='1', run_seed='1', timeout_s=20).start()
        try:
            command = ['sh', '-c', 'test ! -S "$1" && test ! -S "$2" && '
                       'exec python3 action_policy_client.py run --instruction Move --steps 3 --image-dir views',
                       'sh', str(model.path), str(relay.path)]
            plan = sandbox.command(command, platform=platform, work_root=work,
                                   task_dir=task, env={'PATH': os.environ['PATH'], 'HOME': str(task)},
                                   repo=repo, network='none', tool_socket=route.path,
                                   private_paths=(model.path, relay.path))
            process = subprocess.run(plan, capture_output=True, text=True, timeout=20, cwd=task)
            assert process.returncode == 0, process.stderr
            output = json.loads(process.stdout)
            assert output['executed_steps'] == 3
            assert route.summary['clipped_values'] == 36
            assert (task / output['images']['camera']).read_bytes() == base64.b64decode(png)
        finally:
            route.close()
