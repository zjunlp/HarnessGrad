"""Exercise MiniAgent's real native loop through its public subprocess contract."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'base_harness' / 'miniagent'


def invoke(tmp_path, *, harness=HARNESS, task=None, env=None):
    work = tmp_path / 'work'
    work.mkdir(parents=True, exist_ok=True)
    task_file = tmp_path / 'task.json'
    task_file.write_text(json.dumps(task or {'task_id': 'adapter-smoke', 'goal': 'Return the requested result.'}))
    child = dict(os.environ)
    for key in list(child):
        if key.startswith(('HG_AGENT_', 'HG_METHOD_')):
            child.pop(key)
    child.update(HG_AGENT_BACKEND='mock', HG_AGENT_MAX_STEPS='3')
    child.update(env or {})
    proc = subprocess.run([sys.executable, str(harness / 'agent.py'), '--task', str(task_file),
                           '--workdir', str(work)], env=child, capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=20)
    trace = work / 'trace.jsonl'
    rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    return proc, work, rows


def load_adapter():
    spec = importlib.util.spec_from_file_location('miniagent_hg_adapter', HARNESS / 'agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pinned_native_sources_are_unmodified():
    provenance = json.loads((HARNESS / 'PROVENANCE.json').read_text())
    assert provenance['commit'] == 'dea1b1d0285e3328234a1e344562bb9e54ce14f7'
    assert provenance['native_modifications'] == []
    assert len(provenance['source_files_sha256']) == 19
    for path, expected in provenance['source_files_sha256'].items():
        assert hashlib.sha256((HARNESS / path).read_bytes()).hexdigest() == expected
    assert 'Apache License' in (HARNESS / 'FLASHRSI-LICENSE.txt').read_text()


@pytest.mark.parametrize('model', ['miniagent-mock', 'gpt-5-mini'])
def test_mock_uses_native_model_tool_observation_round_trip(tmp_path, model):
    proc, work, rows = invoke(tmp_path, env={'HG_AGENT_MODEL': model})
    assert proc.returncode == 0, proc.stderr
    assert (work / 'answer.txt').read_text() == 'MINIAGENT_MOCK\n'
    actions = [row for row in rows if 'command' in row]
    assert len(actions) == 1
    assert actions[0]['observation']['returncode'] == 0
    assert len([row for row in rows if 'reply' in row]) == 2
    assert [row['usage'] for row in rows if 'usage' in row][-1] == {
        'calls': 2, 'tokens_complete': True, 'input_tokens': 0, 'output_tokens': 0}
    from tools.check_trace_contract import check
    assert not check((work / 'trace.jsonl').read_text())
    assert any(row.get('submitted') and row['answer_source'] == 'artifact' for row in rows)


def test_candidate_runtime_and_skills_are_the_files_that_execute(tmp_path):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    tool = candidate / 'tools' / 'shell.py'
    tool.write_text(tool.read_text().replace('command = args.get("command")',
                    'command = args.get("command").replace("MINIAGENT_MOCK", "EDITED_CANDIDATE")'))
    proc, work, rows = invoke(tmp_path, harness=candidate)
    assert proc.returncode == 0, proc.stderr
    assert (work / 'answer.txt').read_text() == 'EDITED_CANDIDATE\n'
    assert any('miniagent_event' in row for row in rows)


def test_file_submission_cannot_be_replaced_by_final_chat(tmp_path):
    proc, work, rows = invoke(tmp_path, task={'task_id': 'file-smoke', 'goal': 'Write report.json.',
                             'submission': {'kind': 'files', 'paths': ['report.json']}})
    assert proc.returncode != 0
    assert any('missing submission files' in row.get('error', '') for row in rows)
    assert not any(row.get('submitted') for row in rows)


def test_existing_answer_is_not_credited_to_new_run(tmp_path):
    adapter = load_adapter()
    (tmp_path / 'answer.txt').write_text('STALE')
    before = adapter.answer_stamp(tmp_path)
    answer, source = adapter.save_answer({}, tmp_path, before, 'NEW')
    assert (answer, source) == ('NEW', 'final_message')
    assert (tmp_path / 'answer.txt').read_text() == 'NEW'


def test_native_worker_keeps_platform_dependency_overlay(tmp_path):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    overlay = tmp_path / 'dependency-overlay'
    overlay.mkdir()
    (overlay / 'hg_overlay_dependency.py').write_text(
        "MARKER = 'OVERLAY_DEPENDENCY_LOADED'\n", encoding='utf-8')
    tool = candidate / 'tools' / 'shell.py'
    tool.write_text(tool.read_text().replace('command = args.get("command")',
        'from hg_overlay_dependency import MARKER\n'
        '    command = args.get("command").replace("MINIAGENT_MOCK", MARKER)'),
        encoding='utf-8')
    proc, work, rows = invoke(tmp_path, harness=candidate,
                              env={'PYTHONPATH': str(overlay)})
    assert proc.returncode == 0, proc.stderr
    assert (work / 'answer.txt').read_text() == 'OVERLAY_DEPENDENCY_LOADED\n'


def test_symlink_submission_is_refused(tmp_path):
    adapter = load_adapter()
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_text('private')
    (tmp_path / 'report.json').symlink_to(outside)
    with pytest.raises(ValueError, match='missing submission'):
        adapter.save_answer({'submission': {'kind': 'files', 'paths': ['report.json']}}, tmp_path, None, 'OK')


def test_child_environment_is_fresh_and_method_credentials_are_removed(tmp_path):
    adapter = load_adapter()
    env = adapter.child_environment({'HOME': '/operator', 'OPENAI_API_KEY': 'private',
            'HG_AGENT_API_KEY': 'selected', 'HG_METHOD_SECRET': 'method', 'HG_ENV_API_KEY': 'env'}, tmp_path)
    assert env['HOME'] == str(tmp_path / 'home')
    assert env['HG_AGENT_API_KEY'] == 'selected'
    assert not any(key in env for key in ('OPENAI_API_KEY', 'HG_METHOD_SECRET', 'HG_ENV_API_KEY'))


@contextmanager
def scripted_endpoint(*, omit_usage=False):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            # Auxiliary side calls have no tools. Primary turn 1 executes the
            # native shell; primary turn 2 returns the result to the harness.
            if not body.get('tools') or body.get('tool_choice') == 'none':
                message = {'role': 'assistant', 'content': 'SIDE_RESULT'}
                finish = 'stop'
            elif any(m.get('role') == 'tool' for m in body['messages']):
                message = {'role': 'assistant', 'content': 'FINAL_RESULT'}
                finish = 'stop'
            else:
                message = {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'live_shell', 'type': 'function', 'function': {'name': 'shell',
                    'arguments': json.dumps({'command': "printf 'LIVE_ARTIFACT\\n' > answer.txt"})}}]}
                finish = 'tool_calls'
            payload = {'id': 'live_response', 'object': 'chat.completion', 'created': 0,
                'model': body['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}]}
            if not omit_usage:
                payload['usage'] = {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}
            data = json.dumps(payload).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally: server.shutdown(); server.server_close()


def test_live_sdk_primary_and_side_calls_share_selected_route_and_usage(tmp_path):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    (candidate / 'middleware').mkdir()
    (candidate / 'middleware' / 'side.py').write_text('''async def before_llm(messages, ctx):
    if ctx.turn == 1:
        await ctx.side_text("check auxiliary call", purpose="transport-test")
    return messages
''')
    config = candidate / 'agent.yaml'
    config.write_text(config.read_text().replace('middlewares: []', 'middlewares: [side]'))
    with scripted_endpoint() as (base, requests):
        proc, work, rows = invoke(tmp_path, harness=candidate, env={
            'HG_AGENT_BACKEND': 'openai', 'HG_AGENT_MODEL': 'selected-model',
            'HG_AGENT_BASE_URL': base, 'HG_AGENT_API_KEY': 'selected-test-key'})
    assert proc.returncode == 0, proc.stderr
    assert len(requests) == 3
    assert {request['model'] for request in requests} == {'selected-model'}
    assert (work / 'answer.txt').read_text() == 'LIVE_ARTIFACT\n'
    usage = [row['usage'] for row in rows if 'usage' in row][-1]
    assert usage == {'calls': 3, 'tokens_complete': True, 'input_tokens': 21, 'output_tokens': 9}
    assert 'selected-test-key' not in (work / 'trace.jsonl').read_text()


def test_missing_provider_usage_is_unknown(tmp_path):
    with scripted_endpoint(omit_usage=True) as (base, requests):
        proc, work, rows = invoke(tmp_path, env={'HG_AGENT_BACKEND': 'openai',
            'HG_AGENT_MODEL': 'selected-model', 'HG_AGENT_BASE_URL': base})
    assert proc.returncode == 0, proc.stderr
    usage = [row['usage'] for row in rows if 'usage' in row][-1]
    assert usage == {'calls': 2, 'tokens_complete': False}


def test_context_budget_refusal_does_not_make_an_http_call(tmp_path):
    with scripted_endpoint() as (base, requests):
        proc, work, rows = invoke(tmp_path, env={'HG_AGENT_BACKEND': 'openai',
            'HG_AGENT_MODEL': 'selected-model', 'HG_AGENT_BASE_URL': base,
            'HG_AGENT_CONTEXT_WINDOW': '2049', 'HG_AGENT_MAX_OUTPUT_TOKENS': '2048'})
    assert proc.returncode != 0
    assert not requests
    assert any(row.get('error') for row in rows)


def test_timeout_keeps_error_and_usage(tmp_path):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    tool = candidate / 'tools' / 'shell.py'
    tool.write_text(tool.read_text().replace('command = args.get("command")', 'command = "sleep 20"'))
    proc, work, rows = invoke(tmp_path, harness=candidate, env={'HG_AGENT_MINIAGENT_TIMEOUT_S': '1'})
    assert proc.returncode != 0
    assert any('timed out' in row.get('error', '') for row in rows)
    assert any('usage' in row for row in rows)


def test_answer_artifact_symlink_is_refused_before_read(tmp_path):
    adapter = load_adapter()
    external = tmp_path.parent / (tmp_path.name + '-external-answer')
    external.write_text('PRIVATE_EXTERNAL_ANSWER')
    (tmp_path / 'answer.txt').symlink_to(external)
    with pytest.raises(ValueError, match='regular file inside workdir'):
        adapter.answer_stamp(tmp_path)
    with pytest.raises(ValueError, match='regular file inside workdir'):
        adapter.save_answer({}, tmp_path, None, 'final')


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux subreaper /proc check')
def test_native_detached_background_service_is_reaped(tmp_path):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    tool = candidate / 'tools' / 'shell.py'
    script = '''import pathlib,subprocess,sys
p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], start_new_session=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
pathlib.Path('service.pid').write_text(str(p.pid))
pathlib.Path('answer.txt').write_text('SERVICE_STARTED')
'''
    import shlex
    command = shlex.quote(sys.executable) + ' -c ' + shlex.quote(script)
    tool.write_text(tool.read_text().replace('command = args.get("command")', f'command = {command!r}'))
    proc, work, rows = invoke(tmp_path, harness=candidate)
    assert proc.returncode == 0, proc.stderr
    service = int((work / 'service.pid').read_text())
    try:
        assert not Path(f'/proc/{service}').exists(), f'detached service {service} survived completion'
    finally:
        if Path(f'/proc/{service}').exists():
            import signal
            try: os.kill(service, signal.SIGKILL)
            except ProcessLookupError: pass


@pytest.mark.parametrize("tool,kind", [
    ("shell", "shell"), ("read_skill", "filesystem"), ("web_search", "api"),
    ("something_new", "other"),
])
def test_observation_kind_is_the_contracts_vocabulary(tool, kind):
    """Same vocabulary as the other five adapters; the native tool name stays in `tool`."""
    spec = importlib.util.spec_from_file_location('miniagent_hg_bridge',
                                                  HARNESS / 'bridge.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert bridge.observation_kind(tool) == kind
