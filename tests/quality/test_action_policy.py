"""Exercise composition and isolation without loading a model or simulator."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier, Event
import time

import pytest

from data.action_policy_ports.robocasa365 import environment
from eval.service.action_policy import bind_setups, EpisodePolicy, PROTOCOL
from eval.service.tool_transport import Relay, call
from harnessgrad.provenance import verification_context


def fixture_service(path, *, control=None, events=None, fail=False, value=0.0, before_predict=None):
    env = environment()
    identity = {'protocol': PROTOCOL, 'control': control or env['control'],
                'cameras': env['cameras'], 'states': env['states'],
                'actions': list(env['actions']), 'chunk_size': 2}

    def handle(op, args):
        if op == 'describe':
            return identity
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


def episode(path, *, horizon=10):
    actions_taken = []

    def public_observation():
        step = len(actions_taken)
        return {'step': step, 'horizon': horizon, 'ended': step >= horizon,
                'images': {'left': str(step)}, 'state': {'position': [step]}, 'instruction': 'Original goal.'}

    def handle(op, args):
        if op == 'step_many':
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
            assert route.fault == 'ToolTransportError'
            assert call(env.path, 'observe')['step'] == 0
        finally:
            route.close()


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
