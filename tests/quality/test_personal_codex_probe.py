"""Personal Codex diagnostics use only explicit binaries and owned synthetic data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tools.diagnostics import probe_personal_codex as probe
from tools.diagnostics.personal_codex_fixture import (
    AppServer, MODEL, ScriptedResponses, decode, message_text, tool_outputs,
)


def post(base_url, payload, *, headers=None):
    request = Request(base_url + '/responses', data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json', **(headers or {})})
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return response.read().decode()


def fake_process(tmp_path, source):
    program = tmp_path / 'app_server.py'
    program.write_text(source, encoding='utf-8')
    return AppServer([sys.executable, '-S', str(program)], cwd=tmp_path,
                     env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                     deadline=time.monotonic() + 10)


def test_scripted_endpoint_is_bounded_and_preserves_call_result_order():
    call = {'name': 'fixture_tool', 'arguments': {'revision': 'v1'}}
    with ScriptedResponses([call]) as endpoint:
        first = post(endpoint.base_url, {'model': MODEL, 'input': []})
        assert '"name": "fixture_tool"' in first
        second = post(endpoint.base_url, {'model': MODEL, 'input': [
            {'type': 'function_call_output', 'output': 'synthetic-result'}]})
        assert 'Synthetic turn completed.' in second
        assert tool_outputs(endpoint.requests[-1]['payload']) == 'synthetic-result'
        with pytest.raises(HTTPError) as exc:
            post(endpoint.base_url, {'model': MODEL})
        assert exc.value.code == 400 and len(endpoint.requests) == 2


@pytest.mark.parametrize('payload,headers', [
    ({'model': 'a-real-model-is-not-allowed'}, {}),
    ({'model': MODEL}, {'Authorization': 'Bearer synthetic-private-credential'}),
])
def test_unexpected_model_or_credential_never_becomes_a_success(payload, headers):
    with ScriptedResponses([{'name': 'x', 'arguments': {}}]) as endpoint:
        with pytest.raises(HTTPError) as exc:
            post(endpoint.base_url, payload, headers=headers)
        assert exc.value.code == 400
        assert endpoint.requests == []
        assert 'synthetic-private-credential' not in str(endpoint.errors)


def test_evidence_only_counts_input_text_and_returned_tool_output():
    payload = {'tools': [{'description': 'MEMORY_MARKER'}], 'input': [
        {'type': 'function_call', 'arguments': 'SKILL_MARKER'},
        {'type': 'message', 'content': [{'type': 'input_text', 'text': 'real-context'}]},
        {'type': 'function_call_output', 'output': 'real-tool-result'},
    ]}
    assert message_text(payload) == 'real-context'
    assert tool_outputs(payload) == 'real-tool-result'
    assert 'MARKER' not in message_text(payload)


@pytest.mark.parametrize('raw', [b'[]\n', b'{"id":1,"id":2}\n'])
def test_appserver_rejects_invalid_protocol_but_preserves_original_output(tmp_path, raw):
    server = fake_process(tmp_path,
                          f'import sys\nsys.stdout.buffer.write({raw!r})\nsys.stdout.flush()\n')
    try:
        with pytest.raises(RuntimeError, match='invalid or oversized'):
            server.receive()
    finally:
        server.close()
    assert bytes(server.stdout) == raw


def test_appserver_does_not_accept_another_turn_completion(tmp_path):
    server = fake_process(tmp_path,
        'import json,sys\n'
        'print(json.dumps({"method":"turn/completed","params":'
        '{"threadId":"foreign","turn":{"id":"foreign","status":"completed"}}}),flush=True)\n'
        'sys.stdin.read()\n')
    try:
        with pytest.raises(RuntimeError, match='different probe turn'):
            server.wait_turn('ours', 'ours', lambda _: {})
    finally:
        server.close()


def test_malformed_shutdown_output_is_not_hidden_by_an_earlier_success(tmp_path):
    server = fake_process(tmp_path,
        'import json,sys\n'
        'request=json.loads(sys.stdin.readline())\n'
        'print(json.dumps({"id":request["id"],"result":{"ok":True}}),flush=True)\n'
        'sys.stdin.read()\nprint("[]",flush=True)\n')
    try:
        assert server.request('probe', {}) == {'ok': True}
    finally:
        server.close()
    assert server.failure == 'invalid or oversized app-server transcript'
    assert bytes(server.stdout).splitlines()[-1] == b'[]'


def test_tool_request_before_turn_ack_is_deferred_and_answered_once(tmp_path):
    source = '''import json,sys
request=json.loads(sys.stdin.readline())
print(json.dumps({'id':99,'method':'item/tool/call','params':{'threadId':'t','turnId':'u','tool':'fixture'}}),flush=True)
print(json.dumps({'id':request['id'],'result':{'turn':{'id':'u'}}}),flush=True)
reply=json.loads(sys.stdin.readline())
assert reply=={'id':99,'result':{'success':True}}
print(json.dumps({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}}),flush=True)
sys.stdin.read()
'''
    server = fake_process(tmp_path, source)
    handled = []
    try:
        assert server.request('turn/start', {}) == {'turn': {'id': 'u'}}

        def handler(params):
            handled.append(params)
            return {'success': True}

        assert server.wait_turn('t', 'u', handler)['status'] == 'completed'
        assert len(handled) == 1
    finally:
        server.close()


def test_environment_does_not_inherit_credentials_or_startup_injection(tmp_path, monkeypatch):
    for name in ('OPENAI_API_KEY', 'HG_METHOD_API_KEY', 'DEEPSEEK_API_KEY',
                 'CODEX_API_KEY', 'NODE_OPTIONS', 'PYTHONPATH', 'HTTPS_PROXY'):
        monkeypatch.setenv(name, 'synthetic-private-value')
    env = probe.child_environment(tmp_path / 'home', tmp_path / 'tmp', Path(sys.executable))
    assert env['CODEX_HOME'] == str(tmp_path / 'home')
    assert 'synthetic-private-value' not in env.values()
    assert os.environ['HG_METHOD_API_KEY'] == 'synthetic-private-value'


def test_workflow_reports_missing_and_complete_files_without_changing_the_bundle(tmp_path):
    target = probe.bundle(tmp_path, 'v1')
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'manifest.json').write_text(json.dumps({'files': ['report.md']}))
    env = probe.child_environment(tmp_path / 'home', tmp_path, Path(sys.executable))
    params = {'tool': probe.TOOL_NAME, 'arguments': {'revision': 'v1'}}
    before = probe.snapshot.collect(target)
    failed = decode(probe.run_workflow(target, project, env, params)['contentItems'][0]['text'])
    assert failed['missing'] == ['report.md'] and failed['exit_code'] == 1
    (project / 'report.md').write_text('Synthetic report.')
    passed = decode(probe.run_workflow(target, project, env, params)['contentItems'][0]['text'])
    assert passed['missing'] == [] and passed['exit_code'] == 0
    assert probe.snapshot.collect(target) == before


@pytest.mark.parametrize('params', [
    {'tool': probe.TOOL_NAME, 'arguments': {'revision': 'another'}},
    {'tool': probe.TOOL_NAME, 'arguments': {'revision': 'v1', 'command': 'arbitrary'}},
    {'tool': 'another_tool', 'arguments': {'revision': 'v1'}},
])
def test_tool_cannot_choose_another_program_or_revision(tmp_path, monkeypatch, params):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('unexpected process'))
    with pytest.raises(ValueError, match='selected fixture revision'):
        probe.run_workflow(tmp_path / 'v1', tmp_path, {}, params)


@pytest.mark.parametrize('timeout', [False, 0, -1, float('nan'), float('inf'), 301])
def test_invalid_limits_fail_before_creating_output(tmp_path, timeout):
    output = tmp_path / 'new-output'
    with pytest.raises(ValueError, match='timeout'):
        probe.probe(Path(sys.executable), output, timeout=timeout)
    assert not output.exists()


def test_output_cannot_overwrite_existing_user_data_or_enter_checkout(tmp_path, monkeypatch):
    marker = tmp_path / 'keep.txt'
    marker.write_text('user-data')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('unexpected process'))
    with pytest.raises(FileExistsError):
        probe.probe(Path(sys.executable), tmp_path)
    with pytest.raises(ValueError, match='outside the checkout'):
        probe.probe(Path(sys.executable), probe.ROOT / 'uncreated-phase0-output')
    assert marker.read_text() == 'user-data'


@pytest.mark.skipif(not os.environ.get('HG_PERSONAL_CODEX_TEST_EXECUTABLE'),
                    reason='native Codex test requires an explicitly selected executable')
def test_selected_native_codex_loads_updates_restores_and_calls_real_fixture_code(tmp_path):
    report = probe.probe(Path(os.environ['HG_PERSONAL_CODEX_TEST_EXECUTABLE']), tmp_path / 'probe')
    assert report['status'] == 'measured'
    assert report['model']['real_model_calls'] == 0 and report['model']['scripted_requests'] == 6
    assert [case['revision'] for case in report['cases']] == ['v1', 'v2', 'v1']
    assert all(case['workflow_behavior_matches'] for case in report['cases'])
    assert report['cases'][0]['workflow_results'][0]['exit_code'] == 1
    checks = {row['scope']: row for row in report['checks']}
    assert checks['codex.extra_roots_project_isolation']['status'] == 'refused'
    assert checks['codex.separate_profile_process']['status'] == 'ready'
    assert not checks['codex.native_hook_execution']['evidence']['hook_ran']
