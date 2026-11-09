"""Offline controls for isolated OpenClaw delegation and accounting."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import time

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


def test_gateway_configuration_is_private_and_disables_unrelated_services():
    gateway = module('task_gateway')
    config = {'gateway': {'port': 18789}, 'channels': {'fixture': {}}, 'hooks': {'enabled': True}}
    port = gateway.configure(config)
    assert port > 0 and config['gateway']['port'] == port
    assert config['gateway']['bind'] == 'loopback' and config['gateway']['auth']['mode'] == 'token'
    assert config['gateway']['auth']['token']
    assert 'channels' not in config and 'hooks' not in config
    assert config['plugins'] == {'enabled': False} and config['browser'] == {'enabled': False}
    assert config['cron'] == {'enabled': False} and config['discovery']['mdns']['mode'] == 'off'
    assert config['agents']['defaults']['heartbeat']['every'] == '0m'
    newer = {}
    gateway.configure(newer, profile='entries')
    assert newer['models']['catalogRefresh']['enabled'] is False


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
    children, complete = gateway.collect(tmp_path, parent(key), deadline=time.monotonic() + 1)
    assert complete and len(children) == 1
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
    children, complete = gateway.collect(tmp_path, parent(key), deadline=time.monotonic() + .01)
    assert children == [] and not complete
    records = []
    agent.translate_trace([{'message': {'role': 'assistant', 'content': [],
        'usage': {'input': 5, 'output': 1, 'cacheRead': 0, 'cacheWrite': 0}}}],
        records.append, backend='openai', children=children, children_complete=complete)
    assert records[-1]['usage']['input_tokens'] is None
    assert records[-1]['usage']['source'] == 'openclaw_subagent_usage_incomplete'


def test_child_session_symlink_is_not_read(tmp_path):
    gateway = module('task_gateway')
    outside = tmp_path / 'outside'; outside.write_text('private')
    (tmp_path / 'bad').symlink_to(outside)
    with pytest.raises(OSError):
        gateway._read_owned(tmp_path, ('bad',))


def test_newer_gateway_dispatch_and_sqlite_registry_are_collected(tmp_path):
    gateway = module('task_gateway')
    key = 'agent:main:subagent:fixture-child'
    rows = [
        {'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'c1',
            'name': 'dispatch_action', 'arguments': {'id': 'openclaw:core:sessions_spawn'}}]}},
        {'message': {'role': 'toolResult', 'toolCallId': 'c1', 'toolName': 'dispatch_action',
            'details': {'result': {'details': {'status': 'accepted', 'childSessionKey': key}}}}},
    ]
    assert gateway.accepted_children(rows) == ({key}, False)
    directory = tmp_path / 'state'; directory.mkdir()
    db = sqlite3.connect(directory / 'openclaw.sqlite')
    db.execute('CREATE TABLE subagent_runs (run_id TEXT, payload_json TEXT)')
    db.execute('INSERT INTO subagent_runs VALUES (?, ?)', ('r1', json.dumps({
        'childSessionKey': key, 'execution': {'status': 'terminal', 'endedAt': 2,
                                              'outcome': {'status': 'ok'}},
        'delivery': {'status': 'delivered'}})))
    db.commit(); db.close()
    result, complete = gateway.collect(tmp_path, rows, deadline=time.monotonic() + 1,
                                       profile='entries', export_child=lambda k: [{'message': {'role': 'assistant'}}])
    assert complete and result[0]['session_key'] == key and result[0]['outcome']['status'] == 'ok'


def test_missing_or_ambiguous_spawn_result_is_not_complete():
    gateway = module('task_gateway')
    rows = [{'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'c1',
             'name': 'sessions_spawn', 'arguments': {}}]}}]
    assert gateway.accepted_children(rows) == (set(), True)
