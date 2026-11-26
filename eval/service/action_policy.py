"""Bind a trusted local policy to a sandboxed episode's public controls."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import re
from pathlib import Path
import shutil
import socket as sockets
import stat
import tempfile
import threading
import time
from weakref import WeakValueDictionary

from data.scaffolds.asset_module import load
from eval.service.tool_transport import Relay, ToolCallTimeout, ToolTransportError, call


PROTOCOL = 'harnessgrad.action-policy.v1'
PREDICT_TIMEOUT_S = 60.0
CLIENT = Path(__file__).with_name('action_policy_client.py')
PROMPT = (
    'A learned robot controller is available. Use it to execute the task instruction or a subgoal. '
    'For example: python3 action_policy_client.py run --instruction "<task instruction or subgoal>" '
    '--steps 32 --image-dir views. Inspect the returned images and actual step count before '
    'deciding whether to continue, revise the instruction, or finish. The step count here '
    'is an example; choose how long to delegate based on the task.'
)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()).hexdigest()


def bind_setups(socket, dataset, task_ids, setups, envs, *, sandbox):
    """Freeze the service, environment and client in the verification context."""
    if not re.fullmatch(r'[a-z][a-z0-9_]*', dataset):
        raise ValueError('invalid action policy dataset name')
    port = Path(__file__).resolve().parents[2] / 'data/action_policy_ports' / f'{dataset}.py'
    if not port.is_file():
        raise ValueError(f'{dataset} has no public action policy environment port')
    environment = load(port).environment()
    path = Path(socket).absolute()
    if (path.resolve() != path or not stat.S_ISSOCK(path.stat().st_mode)
            or path.parent.stat().st_mode & 0o077):
        raise ValueError('action policy requires a UNIX socket in a private local directory')
    for task_id in task_ids:
        setup = setups.get(task_id) or {}
        env = envs.get(task_id) or {}
        if (not sandbox or env.get('kind') != 'files' or env.get('network') != 'none'
                or not setup.get('tool_relay') or setup.get('workers')):
            raise ValueError(f'{task_id} does not declare a sandboxed native action policy environment')
    identity = call(path, 'describe')
    if (not isinstance(identity, dict) or identity.get('protocol') != PROTOCOL
            or any(not isinstance(identity.get(key), list) or any(not isinstance(v, str) for v in identity[key])
                   for key in ('cameras', 'states', 'actions'))
            or type(identity.get('chunk_size')) is not int or identity['chunk_size'] < 1):
        raise ValueError('invalid action policy service description')
    if (identity.get('control') != environment['control']
            or set(identity.get('cameras', ())) != set(environment['cameras'])
            or set(identity.get('states', ())) != set(environment['states'])
            or set(identity.get('actions', ())) != set(environment['actions'])
            or identity['chunk_size'] > environment['max_chunk']):
        raise ValueError(f'{dataset} has an incompatible control or observation contract')
    binding = {'socket': str(path), 'identity': identity, 'sha256': digest(identity),
               'route_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    bound = {}
    for task_id in task_ids:
        setup = setups[task_id]
        files = list(setup.get('files') or ())
        if any(item['path'] == CLIENT.name for item in files):
            raise ValueError(f'{task_id} already uses the action policy client path')
        files.append({'path': CLIENT.name, 'from': str(CLIENT)})
        prompt = '\n\n'.join(filter(None, (setup.get('runtime_prompt'), PROMPT)))
        bound[task_id] = {**setup, 'files': files, 'runtime_prompt': prompt,
                          'action_policy': binding, 'action_policy_environment': environment}
    return bound


class _EpisodeEnded(Exception):
    """The episode closed or ran out of time before dispatching this call."""


class _EpisodeDeadline(_EpisodeEnded):
    """The episode budget expired before dispatch."""


class _PredictionQueue:
    """Serialize predictions to a socket within this HarnessGrad process."""

    _queues = WeakValueDictionary()
    _registry_lock = threading.Lock()

    @classmethod
    def for_socket(cls, socket):
        with cls._registry_lock:
            queue = cls._queues.get(socket)
            if queue is None:
                queue = cls()
                cls._queues[socket] = queue
            return queue

    def __init__(self):
        self.condition = threading.Condition()
        self.tickets = deque()

    def predict(self, predict, args, *, remaining, summary):
        ticket = object()
        queued_at = time.monotonic()
        with self.condition:
            self.tickets.append(ticket)
        try:
            with self.condition:
                try:
                    while self.tickets[0] is not ticket:
                        self.condition.wait(timeout=remaining())
                except _EpisodeDeadline:
                    summary['queue_timeouts'] += 1
                    raise
                finally:
                    summary['queue_seconds'] += time.monotonic() - queued_at
            return predict(args)
        finally:
            with self.condition:
                self.tickets.remove(ticket)
                self.condition.notify_all()

    def wake_waiters(self):
        with self.condition:
            self.condition.notify_all()


class EpisodePolicy:
    """Compose a single episode relay and a shared, stateless prediction service."""

    def __init__(self, setup, environment_socket, *, task_id, trial_id, run_seed, timeout_s):
        self.binding = setup['action_policy']
        self.prediction_queue = _PredictionQueue.for_socket(self.binding['socket'])
        self.environment = setup['action_policy_environment']
        self.environment_socket = environment_socket
        self.seed = digest([run_seed, task_id, trial_id or 'trial-1'])
        self.deadline = time.monotonic() + timeout_s
        self.closed = threading.Event()
        self.connection_lock = threading.Lock()
        self.connection = None
        self.summary = {'sha256': self.binding['sha256'], 'requests': 0, 'predictions': 0, 'queue_timeouts': 0,
                        'executed_steps': 0, 'clipped_values': 0, 'queue_seconds': 0.0,
                        'policy_seconds': 0.0, 'inference_seconds': 0.0, 'environment_seconds': 0.0}
        self.fault = None
        self.directory = Path(tempfile.mkdtemp(prefix='hg-policy-'))
        self.relay = Relay(self.directory / 'tool.sock', self.handle, max_calls=None)

    @property
    def path(self):
        return self.relay.path

    def start(self):
        try:
            self.relay.start()
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        with self.connection_lock:
            if self.closed.is_set():
                return
            self.closed.set()
            if self.connection is not None:
                try:
                    self.connection.shutdown(sockets.SHUT_RDWR)
                except OSError:
                    pass
        self.prediction_queue.wake_waiters()
        self.relay.stop()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _check_open(self):
        if self.closed.is_set():
            raise _EpisodeEnded()

    def _remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise _EpisodeDeadline()
        self._check_open()
        return seconds

    @contextmanager
    def _connection(self, sock):
        with self.connection_lock:
            self._check_open()
            self.connection = sock
        try:
            yield self._check_open
        finally:
            with self.connection_lock:
                self.connection = None

    def _request(self, path, op, args, *, budget, timing):
        self._remaining()
        started = time.monotonic()
        deadline = min(self.deadline, started + budget)
        try:
            return call(path, op, args, timeout_s=None, deadline=deadline, connection=self._connection)
        except ToolCallTimeout:
            self._record_fault(f'{op}: timeout')
            raise
        except (OSError, ToolTransportError, ValueError) as exc:
            if self.closed.is_set():
                if time.monotonic() >= deadline:
                    self._record_fault(f'{op}: timeout')
            else:
                self._record_fault(f'{op}: {type(exc).__name__}')
            raise
        finally:
            self.summary[timing] += time.monotonic() - started

    def _environment(self, op, args):
        return self._request(self.environment_socket, op, args,
                             budget=math.inf, timing='environment_seconds')

    def _predict(self, args):
        return self._request(self.binding['socket'], 'predict', args,
                             budget=PREDICT_TIMEOUT_S, timing='policy_seconds')

    def handle(self, op, args):
        if op != 'policy.run':
            try:
                return self._environment(op, args)
            except (_EpisodeEnded, OSError, ToolTransportError, ValueError):
                return {'error': 'environment tool could not complete the request'}
        steps = args.get('steps')
        instruction = args.get('instruction')
        if (set(args) != {'instruction', 'steps'} or type(steps) is not int or steps < 1
                or not isinstance(instruction, str) or not instruction.strip()):
            return {'error': 'policy.run requires instruction text and a positive step count'}
        if self.fault:
            return {'error': 'action policy service is unavailable for this episode'}
        self.summary['requests'] += 1
        try:
            return self.run(instruction, steps)
        except (_EpisodeEnded, OSError, ToolTransportError):
            return {'error': 'action policy could not complete the request'}
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self._record_fault(f'invalid action policy or environment reply: {type(exc).__name__}')
            return {'error': 'action policy could not complete the request'}

    def _record_fault(self, reason):
        if self.fault is None:
            self.fault = reason
            self.summary['fault'] = self.fault

    def run(self, instruction, steps):
        cameras = self.environment['cameras']
        observation = self._environment('observe', {'cameras': cameras})
        if 'error' in observation:
            return {**observation, 'executed_steps': 0}
        first = observation['step']
        target = min(first + steps, observation['horizon'])
        while observation['step'] < target and not observation['ended']:
            current_step = observation['step']
            result = self.prediction_queue.predict(self._predict, {
                'identity': self.binding['sha256'], 'instruction': instruction,
                'images': observation['images'], 'state': observation['state'],
                'seed': int(digest([self.seed, current_step])[:16], 16),
            }, remaining=self._remaining, summary=self.summary)
            self.summary['predictions'] += 1
            elapsed = result.get('inference_seconds')
            if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
                self.summary['inference_seconds'] += elapsed
            actions = result['actions']
            if not isinstance(actions, list) or len(actions) != self.binding['identity']['chunk_size']:
                raise ValueError('policy returned a batch of the wrong size')
            submitted = self._format_actions(actions[:target - current_step])
            updated = self._environment('step_many', {'actions': submitted, 'cameras': cameras})
            if 'error' in updated:
                return {**observation, 'executed_steps': current_step - first, 'error': updated['error']}
            advance = updated['step'] - current_step
            if not 0 < advance <= min(len(actions), target - current_step):
                raise ValueError('action policy environment returned inconsistent progress')
            self.summary['executed_steps'] += advance
            observation = updated
        return {**observation, 'executed_steps': observation['step'] - first}

    def _format_actions(self, actions):
        fields = self.environment['actions']
        lower, upper = self.environment['action_bounds']
        submitted = []
        for action in actions:
            if not isinstance(action, dict) or action.keys() != fields.keys():
                raise ValueError('policy returned different action fields')
            safe = {}
            for name, size in fields.items():
                values = action[name]
                if (not isinstance(values, list) or len(values) != size
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
                    raise ValueError('policy returned an invalid action vector')
                safe[name] = [min(upper, max(lower, v)) for v in values]
                self.summary['clipped_values'] += sum(v < lower or v > upper for v in values)
            submitted.append(safe)
        return submitted
