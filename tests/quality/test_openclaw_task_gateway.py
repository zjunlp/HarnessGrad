"""Offline controls for isolated OpenClaw delegation and accounting."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location('_test_' + name, ROOT / 'base_harness/openclaw' / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def parent(key):
    return [{'message': {'role': 'toolResult', 'toolName': 'sessions_spawn',
                         'details': {'status': 'accepted', 'childSessionKey': key}}}]



def test_gateway_configuration_keeps_native_service_configuration():
    gateway = module('task_gateway')
    config = {'gateway': {'port': 18789}, 'channels': {'fixture': {}},
              'hooks': {'enabled': True}, 'plugins': {'enabled': False},
              'browser': {'enabled': True}, 'cron': {'enabled': True}}
    port = gateway.configure(config)
    assert port > 0 and config['gateway']['port'] == port
    assert config['gateway']['bind'] == 'loopback' and config['gateway']['auth']['mode'] == 'token'
    assert config['gateway']['auth']['token']
    assert config['channels'] == {'fixture': {}} and config['hooks'] == {'enabled': True}
    assert config['plugins'] == {'enabled': False} and config['browser'] == {'enabled': True}
    assert config['cron'] == {'enabled': True}
    plugin = 'harnessgrad-retain-subagents'
    newer = {'models': {}, 'plugins': {'allow': ['fixture'], 'deny': ['other'], 'load': {'paths': ['/fixture']},
                                      'entries': {'other': {'enabled': False}}}}
    gateway.configure(newer, profile='entries')
    assert 'catalogRefresh' not in newer['models']
    assert newer['plugins'] == {
        'allow': ['fixture', plugin], 'deny': ['other'],
        'load': {'paths': ['/fixture', str(ROOT / 'base_harness/openclaw' / f'{plugin}.mjs')]},
        'entries': {'other': {'enabled': False}, plugin: {'enabled': True}},
    }


@pytest.mark.parametrize('plugins, setting', [
    ({'enabled': False}, 'plugins.enabled'),
    ({'deny': ['  HARNESSGRAD-RETAIN-SUBAGENTS  ']}, 'plugins.deny'),
    ({'entries': {'harnessgrad-retain-subagents': {'enabled': False}}},
     'plugins.entries.harnessgrad-retain-subagents.enabled'),
    ({'entries': {'HARNESSGRAD-RETAIN-SUBAGENTS': {'enabled': False}}},
     'plugins.entries.harnessgrad-retain-subagents.enabled'),
], ids=['global-disable', 'denylist', 'entry-disable', 'normalized-entry-disable'])
def test_retention_plugin_conflict_stops_before_launch(tmp_path, monkeypatch, plugins, setting):
    gateway, agent = module('task_gateway'), module('agent')
    config = {'plugins': deepcopy(plugins)}
    monkeypatch.setattr(agent, 'configuration', lambda *_, **__: config)
    monkeypatch.setattr(agent, 'gateway_helper', lambda: gateway)
    monkeypatch.setattr(agent, 'runtime_identity', lambda _: {
        'argv': ['fixture-openclaw'], 'node': '/fixture/node', 'source_identity': {'adapter_profile': 'entries'},
    })

    def unexpected_launch(*_, **__):
        pytest.fail('conflicting config started an OpenClaw process')

    monkeypatch.setattr(agent.subprocess, 'Popen', unexpected_launch)
    for name, value in {'HG_AGENT_BACKEND': 'openai', 'HG_AGENT_BASE_URL': 'http://127.0.0.1:1/v1',
                        'HG_AGENT_API_KEY': 'fixture-only', 'HG_AGENT_MODEL': 'fixture-model'}.items():
        monkeypatch.setenv(name, value)

    assert agent.run({'goal': 'Return an answer.'}, tmp_path) == 1
    assert config == {'plugins': plugins}
    rows = [json.loads(line) for line in (tmp_path / 'trace.jsonl').read_text().splitlines()]
    error = next(row['error'] for row in rows if 'error' in row)
    assert setting in error and 'harnessgrad-retain-subagents' in error and 'config.json' in error
    assert rows[-1]['usage']['calls'] == 0


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is unavailable')
def test_task_gateway_retains_subagents_requested_for_deletion():
    plugin = ROOT / 'base_harness/openclaw/harnessgrad-retain-subagents.mjs'
    script = '''
import assert from "node:assert/strict";
const { default: plugin } = await import(process.argv[1]);
plugin.register({ on(name, handle) {
  assert.equal(name, "before_tool_call");
  const spawn = { task: "fixture", cleanup: "delete" };
  const kept = { ...spawn, cleanup: "keep" };
  assert.deepEqual(handle({ toolName: "sessions_spawn", params: spawn }), { params: kept });
  for (const toolName of ["dispatch_action", "tool_call"]) {
    const params = { id: "openclaw:core:sessions_spawn", args: spawn };
    assert.deepEqual(handle({ toolName, params }), { params: { ...params, args: kept } });
    assert.equal(handle({ toolName, params: { ...params, id: "sessions_send" } }), undefined);
  }
  assert.equal(handle({ toolName: "sessions_spawn", params: kept }), undefined);
  assert.equal(handle({ toolName: "sessions_spawn", params: {} }), undefined);
  assert.equal(spawn.cleanup, "delete");
} });
'''
    subprocess.run(['node', '--input-type=module', '-e', script, plugin.as_uri()], check=True, timeout=20)


def test_child_records_are_collected_and_counted_once(tmp_path):
    gateway = module('task_gateway')
    agent = module('agent')
    key = 'agent:main:subagent:fixture-child'
    (tmp_path / 'subagents').mkdir()
    (tmp_path / 'subagents/runs.json').write_text(json.dumps({'runs': {'r': {
        'childSessionKey': key, 'endedAt': 1, 'outcome': {'status': 'ok'}}}}))
    folder = tmp_path / 'agents/main/sessions'; folder.mkdir(parents=True)
    (folder / 'sessions.json').write_text(json.dumps({key: {'sessionId': 'child-1'}}))
    (folder / 'child-1.jsonl').write_text(json.dumps({'message': {'role': 'assistant',
        'content': [{'type': 'text', 'text': 'child response'}],
        'usage': {'input': 10, 'output': 3, 'cacheRead': 2, 'cacheWrite': 0}}}) + '\n')
    deadline = time.monotonic() + 1
    completion = gateway.wait_for_children(tmp_path, parent(key), deadline=deadline)
    assert completion == gateway.ChildCompletion('finished', {key: {'status': 'ok'}})
    children, trace_complete = gateway.collect_child_sessions(tmp_path, completion.outcomes, deadline=deadline)
    assert trace_complete and len(children) == 1
    rows = [{'message': {'role': 'assistant', 'stopReason': 'stop', 'content': [{'type': 'text', 'text': 'parent answer'}],
                         'usage': {'input': 20, 'output': 4, 'cacheRead': 1, 'cacheWrite': 0}}}]
    records = []
    summary = agent.translate_trace(rows, records.append, backend='openai', children=children)
    usage = records[-1]['usage']
    assert summary['final'] == 'parent answer'
    assert usage['input_tokens'] == 30 and usage['output_tokens'] == 7
    assert usage['cache_read_tokens'] == 3 and usage['subagent_sessions'] == 1
    assert any('openclaw_subagent_session' in row for row in records)


def test_pending_tool_preamble_is_not_a_final_answer():
    agent = module('agent'); records = []
    summary = agent.translate_trace([{'message': {'role': 'assistant', 'stopReason': 'toolUse',
        'content': [{'type': 'text', 'text': 'I will delegate now.'}],
        'usage': {'input': 2, 'output': 1, 'cacheRead': 0, 'cacheWrite': 0}}}],
        records.append, backend='openai')
    assert summary['final'] == ''


def test_missing_or_unfinished_child_never_becomes_zero_usage(tmp_path):
    gateway = module('task_gateway'); agent = module('agent')
    key = 'agent:main:subagent:missing'
    completion = gateway.wait_for_children(tmp_path, parent(key), deadline=time.monotonic() + .01)
    assert completion == gateway.ChildCompletion('unknown', {})
    records = []
    agent.translate_trace([{'message': {'role': 'assistant', 'content': [],
        'usage': {'input': 5, 'output': 1, 'cacheRead': 0, 'cacheWrite': 0}}}],
        records.append, backend='openai', children=[], child_traces_complete=False)
    assert records[-1]['usage']['input_tokens'] is None
    assert records[-1]['usage']['source'] == 'openclaw_subagent_usage_incomplete'


def test_child_session_symlink_is_not_read(tmp_path):
    gateway = module('task_gateway')
    outside = tmp_path / 'outside'; outside.write_text('private')
    (tmp_path / 'bad').symlink_to(outside)
    with pytest.raises(OSError):
        gateway._read_owned(tmp_path, ('bad',))


@pytest.mark.parametrize('wrapped, execution_status, delivery_status, expected_status', [
    pytest.param(False, 'terminal', 'delivered', 'finished', id='delivered'),
    pytest.param(True, 'terminal', 'delivered', 'finished', id='parent-completion'),
    pytest.param(False, 'terminal', 'not_required', 'finished', id='no-notification'),
    pytest.param(True, 'terminal', 'not_required', 'finished', id='parent-completion-no-notification'),
    pytest.param(True, 'running', 'not_required', 'pending', id='child-still-running'),
    pytest.param(True, 'terminal', 'pending', 'pending', id='notification-still-pending'),
])
def test_newer_gateway_dispatch_and_sqlite_registry(
    tmp_path, wrapped, execution_status, delivery_status, expected_status,
):
    gateway = module('task_gateway')
    key = 'agent:main:subagent:fixture-child'
    dispatcher = 'tool_call' if wrapped else 'dispatch_action'
    rows = [
        {'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'c1',
            'name': dispatcher, 'arguments': {'id': 'openclaw:core:sessions_spawn'}}]}},
        {'message': {'role': 'toolResult', 'toolCallId': 'c1', 'toolName': dispatcher,
            'details': {'result': {'details': {'status': 'accepted', 'childSessionKey': key}}}}},
    ]
    assert gateway.accepted_children(rows) == ({key}, False)
    execution = {'status': execution_status}
    if execution_status == 'terminal':
        execution.update(endedAt=2, outcome={'status': 'ok'})
    needs_notification = delivery_status != 'not_required'
    record = {
        'childSessionKey': key, 'execution': execution,
        'completion': {'required': needs_notification}, 'expectsCompletionMessage': needs_notification,
        'delivery': {'status': delivery_status},
    }
    if execution_status == 'terminal':
        record['cleanupCompletedAt'] = 3
    if wrapped:
        record['completionTarget'] = 'parent'
        record = {'parentCompletion': record}
    directory = tmp_path / 'state'
    directory.mkdir()
    db = sqlite3.connect(directory / 'openclaw.sqlite')
    with db:
        db.execute('CREATE TABLE subagent_runs (run_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)')
        db.execute('INSERT INTO subagent_runs VALUES (?, ?)', ('r1', json.dumps(record)))
    db.close()

    child_rows = [{'message': {'role': 'assistant'}}]

    def export_child(child_key):
        assert child_key == key
        return child_rows

    deadline = time.monotonic() + 1
    completion = gateway.wait_for_children(tmp_path, rows, deadline=deadline, profile='entries')
    assert completion.execution_status == expected_status
    if expected_status == 'finished':
        children, trace_complete = gateway.collect_child_sessions(
            tmp_path, completion.outcomes, deadline=deadline, profile='entries', export_child=export_child)
        assert trace_complete
        assert children == [{'session_key': key, 'outcome': {'status': 'ok'}, 'rows': child_rows}]
    else:
        assert not completion.outcomes


def test_missing_or_ambiguous_spawn_result_is_not_complete():
    gateway = module('task_gateway')
    rows = [{'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'c1',
             'name': 'sessions_spawn', 'arguments': {}}]}}]
    assert gateway.accepted_children(rows) == (set(), True)


@pytest.mark.parametrize('status, exit_code', [('forbidden', 0), (None, 1)])
def test_submission_after_spawn_refusal_or_missing_result(tmp_path, monkeypatch, status, exit_code):
    gateway, agent = module('task_gateway'), module('agent')
    rows = [{'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'c1',
        'name': 'tool_call', 'arguments': {'id': 'openclaw:core:sessions_spawn',
                                          'args': {'task': 'Help', 'agentId': 'other'}}}]}}]
    if status is not None:
        rows.append({'message': {'role': 'toolResult', 'toolCallId': 'c1', 'toolName': 'tool_call',
            'isError': False, 'details': {'result': {'details': {
                'status': status, 'error': 'agentId is not allowed for sessions_spawn'}}}}})
    rows.append({'message': {'role': 'assistant', 'stopReason': 'stop',
        'content': [{'type': 'text', 'text': 'parent answer'}]}})
    profile = agent._PROFILES['entries']._replace(read_session=lambda *_: rows)
    monkeypatch.setitem(agent._PROFILES, 'entries', profile)
    monkeypatch.setattr(gateway, 'running', lambda *_, **__: nullcontext())
    monkeypatch.setattr(agent, 'gateway_helper', lambda: gateway)
    monkeypatch.setattr(agent, 'runtime_identity', lambda _: {
        'argv': ['fixture-openclaw'], 'node': '/fixture/node', 'source_identity': {'adapter_profile': 'entries'},
    })
    monkeypatch.setattr(agent, 'stop_process', lambda _: None)
    monkeypatch.setattr(agent.subprocess, 'Popen', lambda *_, **__: SimpleNamespace(
        returncode=0, communicate=lambda **_: ('', '')))
    for name, value in {'HG_AGENT_BACKEND': 'openai', 'HG_AGENT_BASE_URL': 'http://127.0.0.1:1/v1',
                        'HG_AGENT_API_KEY': 'fixture-only', 'HG_AGENT_MODEL': 'fixture-model',
                        'HG_AGENT_OPENCLAW_TIMEOUT_S': '2', 'HG_AGENT_DEADLINE_S': '10'}.items():
        monkeypatch.setenv(name, value)

    assert agent.run({'goal': 'Return an answer.', 'submission': {'kind': 'text'}}, tmp_path) == exit_code
    answer = tmp_path / 'answer.txt'
    if status is None:
        assert not answer.exists()
        assert 'OpenClaw subagent completion could not be verified' in (tmp_path / 'trace.jsonl').read_text()
    else:
        assert answer.read_text(encoding='utf-8') == 'parent answer'


@pytest.mark.parametrize('case, status, answer_source, error', [
    ('child-export-failed', 'finished', 'files', None),
    ('child-export-failed', 'finished', 'final_message', None),
    ('child-export-timeout', 'finished', 'final_message', None),
    ('parent-refresh-failed', 'finished', 'files', None),
    ('parent-refresh-failed', 'finished', 'artifact', None),
    ('parent-refresh-failed', 'finished', 'final_message', 'could not refresh the parent session'),
    ('parent-refresh-empty', 'finished', 'final_message', 'could not refresh the parent session'),
    ('parent-wake-finished', 'finished', 'final_message', None),
    ('parent-wake-timeout', 'pending', 'final_message', 'timed out before completion'),
    ('child-pending', 'pending', 'files', 'timed out before completion'),
    ('child-missing', 'unknown', 'files', 'completion could not be verified'),
    ('registry-interrupted', None, 'final_message', 'interrupted by signal'),
    ('parent-refresh-interrupted', None, 'files', 'interrupted by signal'),
    ('child-export-interrupted', None, 'final_message', 'interrupted by signal'),
])
def test_submission_after_delegation(tmp_path, monkeypatch, case, status, answer_source, error):
    gateway, agent = module('task_gateway'), module('agent')
    first, second = ('agent:main:subagent:fixture-a', 'agent:main:subagent:fixture-b')
    registry = {key: {'childSessionKey': key, 'endedAt': 1, 'outcome': {'status': 'ok'},
                      'cleanupCompletedAt': 2, 'delivery': {'status': 'delivered'}}
                for key in (first, second)}
    if case == 'child-pending':
        registry[first].pop('endedAt')
    elif case == 'child-missing':
        del registry[first]
    now = 0
    has_wake = case.startswith('parent-wake-')
    wake_finishes_at = 2 if case == 'parent-wake-timeout' else .3

    def read_registry(*_):
        if case == 'registry-interrupted' and now == 0:
            signal.raise_signal(signal.SIGTERM)
        if not has_wake:
            return registry
        if now == 0:
            before_cleanup = registry[first].copy()
            del before_cleanup['cleanupCompletedAt']
            return {**registry, first: before_cleanup}
        if now >= wake_finishes_at:
            return registry
        wake = {'status': 'pending' if now < .2 else 'dispatching'}
        return {**registry, first: {**registry[first], 'requesterSettleWake': wake}}

    monkeypatch.setattr(gateway, '_registry', read_registry)

    def advance(seconds):
        nonlocal now
        now += seconds

    clock = SimpleNamespace(monotonic=lambda: now, sleep=advance, time_ns=lambda: 0)
    monkeypatch.setattr(gateway, 'time', clock)
    monkeypatch.setattr(agent, 'time', clock)

    def configure(config, **_):
        config['gateway'] = {'auth': {'token': 'fixture-gateway-token'}}
        return 0

    gateway_closed = False

    @contextmanager
    def running(*_, **__):
        nonlocal gateway_closed
        try:
            yield
        finally:
            gateway_closed = True

    monkeypatch.setattr(gateway, 'configure', configure)
    monkeypatch.setattr(gateway, 'running', running)
    monkeypatch.setattr(agent, 'gateway_helper', lambda: gateway)
    monkeypatch.setattr(agent, 'runtime_identity', lambda _: {
        'argv': ['fixture-openclaw'], 'node': '/fixture/node', 'source_identity': {'adapter_profile': 'entries'},
    })
    monkeypatch.setattr(agent, 'stop_process', lambda _: None)
    usage = {'input': 5, 'output': 1, 'cacheRead': 0, 'cacheWrite': 0}
    native = parent(first) + parent(second) + [
        {'message': {'role': 'assistant', 'stopReason': 'stop', 'usage': usage,
                     'content': [{'type': 'text', 'text': 'I will report the result later.'}]}},
    ]
    parent_reads = 0

    def read_parent(_state, _session, _argv, _env, remaining):
        nonlocal parent_reads
        parent_reads += 1
        if remaining <= 0:
            raise agent.AdapterError('fixture parent export ran out of time')
        if parent_reads == 1 or (has_wake and now < wake_finishes_at):
            return native
        if case == 'parent-refresh-interrupted':
            signal.raise_signal(signal.SIGTERM)
        if case == 'parent-refresh-failed':
            raise ValueError('fixture parent export failed')
        if case == 'parent-refresh-empty':
            return []
        return native + [{'message': {'role': 'assistant', 'stopReason': 'stop', 'usage': usage,
                                      'content': [{'type': 'text', 'text': 'final result'}]}}]

    profile = agent._PROFILES['entries']._replace(read_session=read_parent)
    monkeypatch.setitem(agent._PROFILES, 'entries', profile)

    def export_child(_state, key, _argv, _env, remaining, **_kwargs):
        if case == 'child-export-interrupted' and key == first:
            signal.raise_signal(signal.SIGINT)
        if case == 'child-export-failed' and key == first:
            raise ValueError('fixture child export failed')
        if case == 'child-export-timeout' and key == second:
            advance(remaining)
            raise agent.subprocess.TimeoutExpired('fixture child export', remaining)
        return [{'message': {'role': 'assistant', 'usage': usage}}]

    monkeypatch.setattr(agent, '_export_session_rows', export_child)

    def communicate(*_, **__):
        if answer_source == 'files':
            (tmp_path / 'result.json').write_text('{}', encoding='utf-8')
        elif answer_source == 'artifact':
            (tmp_path / 'answer.txt').write_text('artifact result', encoding='utf-8')
        return '', ''

    monkeypatch.setattr(agent.subprocess, 'Popen', lambda *_, **__: SimpleNamespace(
        returncode=0, communicate=communicate))
    for name, value in {'HG_AGENT_BACKEND': 'openai', 'HG_AGENT_BASE_URL': 'http://127.0.0.1:1/v1',
                        'HG_AGENT_API_KEY': 'fixture-only', 'HG_AGENT_MODEL': 'fixture-model',
                        'HG_AGENT_OPENCLAW_TIMEOUT_S': '1', 'HG_AGENT_DEADLINE_S': '10'}.items():
        monkeypatch.setenv(name, value)
    task = {'goal': 'Create the result.', 'submission': {'kind': 'text'}}
    if answer_source == 'files':
        task['submission'] = {'kind': 'files', 'paths': ['result.json']}
    assert agent.run(task, tmp_path) == (0 if error is None else 1)
    assert gateway_closed
    rows = [json.loads(line) for line in (tmp_path / 'trace.jsonl').read_text().splitlines()]
    if status is None:
        assert any(error in row.get('error', '') for row in rows)
        assert not any(row.get('submitted') for row in rows)
        assert not (tmp_path / 'answer.txt').exists()
        return
    complete = has_wake and error is None
    assert next(row['openclaw_delegation'] for row in rows if 'openclaw_delegation' in row) == {
        'execution_status': status, 'trace_complete': complete,
    }
    total = next(row['usage'] for row in rows if 'usage' in row)
    assert (total['input_tokens'], total['output_tokens']) == ((20, 4) if complete else (None, None))
    assert total['parent_session']['input_tokens'] == (10 if complete or case.startswith('child-export-') else 5)
    assert parent_reads == (2 if status == 'finished' else 1)
    assert any(row.get('reply') == 'I will report the result later.' for row in rows)
    if error is None:
        submitted = next(row for row in rows if row.get('submitted'))
        assert submitted['answer_source'] == answer_source
        if answer_source == 'files':
            assert submitted['submitted_files'] == ['result.json']
        else:
            answer = 'artifact result' if answer_source == 'artifact' else 'final result'
            assert submitted['answer'] == (tmp_path / 'answer.txt').read_text(encoding='utf-8') == answer
        collected = first if case == 'child-export-timeout' else second
        assert any(row.get('openclaw_subagent_session', {}).get('session_key') == collected for row in rows)
    else:
        assert not any(row.get('submitted') for row in rows)
        assert any(error in row.get('error', '') for row in rows)
        assert not (tmp_path / 'answer.txt').exists()
