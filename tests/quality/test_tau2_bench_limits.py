"""Actual v0.1.0 Orchestrator.run() vs private runtime: deterministic, no models.

Host Python 3.12 launches the prepared Python 3.13 release. Only scripted message
fixtures are used; source/data storage is read-only and unavailable storage skips.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / 'data/datasets/tau2_bench/lib'


def _available():
    try:
        sys.path.insert(0, str(ROOT))
        from data.datasets import tau2_bench
        python = tau2_bench.RUNTIME / 'bin/python'
        bundle = tau2_bench.DATASET_ROOT / 'bundle'
        if python.is_file() and (bundle / 'manifest.json').is_file():
            return (python, bundle), None
    except Exception:
        return None, 'tau2 dataset storage unavailable'
    return None, 'tau2 pinned runtime/bundle unavailable'


_PROBE = r'''
import json, socket, sys, tempfile
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from bootstrap import bootstrap
bootstrap(Path(sys.argv[2]))
def offline(*a, **kw): raise AssertionError('network forbidden in deterministic fixture')
socket.socket.connect = offline
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.data_model.tasks import Task
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.user.user_simulator import UserSimulator as OfficialUser
import runtime

def tool(name='ok', who='assistant', ident='c1'):
    return ToolCall(id=ident, name=name, arguments={}, requestor=who)
def say(content='answer'): return AssistantMessage(role='assistant', content=content)
def user(content='hello'): return UserMessage(role='user', content=content)
def calls(who='assistant', batch=False):
    cls = AssistantMessage if who == 'assistant' else UserMessage
    return cls(role=who, tool_calls=[tool('bad', who, 'c1')] +
               ([tool('ok', who, 'c2')] if batch else []))

class Environment:
    user_tools = None
    def __init__(self, **kw): self.calls = []; self.syncs = 0
    def set_state(self, **kw): pass
    def sync_tools(self): self.syncs += 1
    def get_response(self, call):
        self.calls.append((call.name, call.requestor))
        return ToolMessage(role='tool', id=call.id, content=call.name,
                           requestor=call.requestor, error=call.name == 'bad')
class ScriptedUser:
    script = []
    def __init__(self, **kw): self.queue = deepcopy(type(self).script)
    def get_init_state(self, message_history=None): return None
    def set_seed(self, seed): pass
    def generate_next_message(self, incoming, state):
        return self.queue.pop(0), state
    is_stop = staticmethod(OfficialUser.is_stop)
class ScriptedAgent:
    def __init__(self, messages): self.queue = deepcopy(messages)
    def set_seed(self, seed): pass
    def get_init_state(self, message_history=None): return None
    def generate_next_message(self, incoming, state): return self.queue.pop(0), state
    @staticmethod
    def is_stop(message): return '###STOP###' in (message.content or '')

# Use the real runtime constructor, message validation, bootstrap, APIs and loops.
# Only model participants and domain tools are replaced with deterministic fixtures.
require = runtime._require_attr
runtime._require_attr = lambda module, attr: (ScriptedUser if attr == 'UserSimulator' else require(module, attr))
runtime._get_tau2_domain_constructor = lambda *args: (Environment, {})

def signature(messages):
    return [[m.role, m.content, [(c.name, c.requestor) for c in (getattr(m, 'tool_calls', None) or [])],
             getattr(m, 'requestor', None), getattr(m, 'error', None)] for m in messages]

def compare(label, budget, errors, users, agents, history=None):
    task_data = {'id': 'fixture', 'user_scenario': {'instructions': 'deterministic fixture'}}
    if history:
        task_data['initial_state'] = {'message_history': [m.model_dump(mode='json') for m in history]}
    task = Task.model_validate(task_data)
    ScriptedUser.script = users
    official = Orchestrator(domain='fixture', agent=ScriptedAgent(agents), user=ScriptedUser(),
        environment=Environment(), task=task, max_steps=budget, max_errors=errors, seed=7)
    # Crucially, no copied limit logic: execute the release's actual complete run().
    released_run = official.run()
    config = {'domain': 'airline', 'task': task_data, 'seed': 7, 'max_steps': budget,
              'max_errors': errors, 'user_model': 'unused-fixture'}
    with tempfile.TemporaryDirectory(prefix='tau2-limits-') as tmp:
        path = Path(tmp) / 'config.json'; path.write_text(json.dumps(config))
        private = runtime.Tau2Runtime(path)
    private.start_conversation()
    scripted = deepcopy(agents)
    while private.termination_reason is None:
        message = scripted.pop(0)
        if message.is_tool_call():
            assert len(message.tool_calls) == 1  # candidate API currently exposes singles
            call = message.tool_calls[0]
            private.call_assistant_tool(call.name, call.arguments)
        else:
            private.submit_assistant_message(message.content)
    expected = {'steps': official.step_count, 'errors': official.num_errors,
                'reason': released_run.termination_reason.value,
                'tools': official.environment.calls, 'syncs': official.environment.syncs,
                'messages': signature(official.trajectory)}
    actual = {'steps': private.step_count, 'errors': private.num_errors,
              'reason': private.termination_reason, 'tools': private.environment.calls,
              'syncs': private.environment.syncs, 'messages': signature(private.messages)}
    assert actual == expected, (label, budget, errors, actual, expected)
    return {'label': label, 'budget': budget, 'max_errors': errors, 'steps': private.step_count,
            'reason': private.termination_reason, 'tool_count': len(private.environment.calls),
            'num_errors': private.num_errors, 'tool_error_count': private.tool_error_count}

rows = []
for budget in range(0, 8):
    rows.append(compare('assistant_tool', budget, 1, [user(), user('###STOP###')], [calls(), say()]))
    rows.append(compare('user_batch', budget, 1, [calls('user', True), user(), user('###STOP###')], [say()]))
for budget, errors in [(1, 10), (10, 0), (1, 0), (10, 10)]:
    rows.append(compare('agent_stop', budget, errors, [], [say('###STOP###')], [user()]))
    rows.append(compare('user_stop', budget, errors, [user('###STOP###')], []))
for who in ('assistant', 'user'):
    for budget in (0, 1, 2, 3, 4):
        agents = [say()]
        users = [user('###STOP###')] if who == 'assistant' else [user(), user('###STOP###')]
        rows.append(compare('pending_' + who, budget, 10, users, agents, [calls(who, True)]))
for who in ('assistant', 'user'):
    initial = say('###STOP###') if who == 'assistant' else user('###STOP###')
    rows.append(compare('initial_stop_' + who, 0, 0, [], [], [initial]))
# The official error counter is never incremented, even though error=True tool
# results are recorded. This is a pinned-release quirk, not permission to fix it.
assert all(row['num_errors'] == 0 for row in rows)
assert any(row['tool_error_count'] > 0 for row in rows)
print(json.dumps({'rows': rows, 'python': list(sys.version_info[:2]), 'release': 'v0.1.0'}))
'''


@pytest.fixture(scope='module')
def differential():
    available, reason = _available()
    if reason:
        pytest.skip(reason)
    python, bundle = available
    proc = subprocess.run([str(python), '-I', '-B', '-c', _PROBE, str(LIB), str(bundle)],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert proc.returncode == 0, proc.stderr[-6000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_actual_official_run_is_differentially_verified(differential):
    assert differential['python'] == [3, 13]
    assert differential['release'] == 'v0.1.0'
    assert len(differential['rows']) == 36


def test_odd_even_limits_do_not_execute_tools_after_emission_budget(differential):
    rows = [r for r in differential['rows'] if r['label'] == 'assistant_tool']
    assert next(r for r in rows if r['budget'] == 2)['tool_count'] == 0
    assert next(r for r in rows if r['budget'] == 3)['tool_count'] == 1


def test_user_batching_and_errors_are_release_faithful(differential):
    row = next(r for r in differential['rows'] if r['label'] == 'user_batch' and r['budget'] == 6)
    assert row['tool_count'] == 2 and row['tool_error_count'] == 1
    assert row['num_errors'] == 0 and row['reason'] == 'user_stop'


def test_limit_precedence_over_stop_matches_official_run(differential):
    row = next(r for r in differential['rows'] if r['label'] == 'agent_stop' and r['budget'] == 1 and r['max_errors'] == 0)
    assert row['reason'] == 'too_many_errors'


def test_initial_stop_is_not_charged_a_step(differential):
    rows = [r for r in differential['rows'] if r['label'].startswith('initial_stop_')]
    assert all(r['steps'] == 0 and r['reason'].endswith('_stop') for r in rows)
