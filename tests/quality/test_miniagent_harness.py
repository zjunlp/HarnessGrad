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
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest

from eval.containers import sandbox
from eval.service.native_model_route import NativeModelRoute
from harnessgrad import config
from harnessgrad import python_harness_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'base_harness' / 'miniagent'


@pytest.fixture(scope='module')
def miniagent_store():
    """Real wheels, offline only; never install SDKs in pytest's platform env."""
    if not shutil.which('uv') or not shutil.which('bwrap'):
        pytest.skip('MiniAgent integration requires uv and bubblewrap')
    root = Path(tempfile.mkdtemp(prefix='hg-miniagent-test-', dir=Path.home() / '.cache'))
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
            patch.setenv('UV_OFFLINE', '1')
            patch.delenv('HG_HARNESS_WHEELHOUSE', raising=False)
            config.reset()
            try:
                runtime.prepare(HARNESS, root=root / 'store')
            except runtime.RuntimeUnavailable as exc:
                # An empty wheel cache or missing Python is a missing test
                # prerequisite. Lock conflicts and actual preparation bugs fail.
                if any(text in str(exc) for text in (
                        'not found in the cache', 'not cached', 'No interpreter found',
                        'No interpreter matching')):
                    pytest.skip('Prepare MiniAgent with configure_harness.py --fetch first: ' + str(exc))
                raise
        yield root
    finally:
        config.reset()
        runtime.remove_instance(root)


@pytest.fixture
def managed_miniagent(miniagent_store, monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(('HG_AGENT_', 'HG_METHOD_', 'HG_HARNESS_')):
            monkeypatch.delenv(key)
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT', str(miniagent_store / 'store'))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_AGENT_MAX_STEPS', '3')
    monkeypatch.setenv('UV_OFFLINE', '1')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    config.reset()
    try:
        yield SimpleNamespace(binding=runtime.identity(HARNESS), root=miniagent_store)
    finally:
        config.reset()


@pytest.fixture
def invoke(managed_miniagent):
    return _invoke


def _invoke(tmp_path, *, harness=HARNESS, task=None, env=None):
    """Retain work artifacts while executing only inside the real sandbox."""
    work = tmp_path / 'work'
    work.mkdir(parents=True, exist_ok=True)
    task_file = work / 'task.json'
    task_file.write_text(json.dumps(task or {'task_id': 'adapter-smoke', 'goal': 'Return the requested result.'}))
    child = dict(os.environ)
    for key in list(child):
        if key.startswith(('HG_AGENT_', 'HG_METHOD_')):
            child.pop(key)
    child.update(HG_AGENT_BACKEND='mock', HG_AGENT_MAX_STEPS='3')
    child.update(env or {})
    binding = runtime.identity(harness)
    artifact = binding['artifact']
    child['PATH'] = str(Path(artifact['python']).parent) + os.pathsep + os.defpath
    child['HOME'] = str(work)
    work_root = tmp_path / 'run-root'
    work_root.mkdir()
    child['HARNESSGRAD_WORK_ROOT'] = str(work_root)
    route = (NativeModelRoute(child['HG_AGENT_BASE_URL']).start()
             if child['HG_AGENT_BACKEND'] != 'mock' else None)
    try:
        proc = sandbox.run([artifact['python'], '-I', '-B', str(harness / 'agent.py'),
                            '--task', str(task_file), '--workdir', str(work)],
            platform=ROOT, work_root=work_root, task_dir=work, repo=harness,
            env=child, cwd=work, timeout_s=20, platform_interpreter=False,
            runtime_paths=tuple(Path(p) for p in artifact['runtime_paths']),
            network='none', model_socket=route.socket_path if route else None,
            model_path=route.path if route else '')
    finally:
        if route:
            route.stop()
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
    assert 'Apache License' in (HARNESS / 'LICENSE.upstream').read_text()


@pytest.mark.parametrize('model', ['miniagent-mock', 'gpt-5-mini'])
def test_mock_uses_native_model_tool_observation_round_trip(tmp_path, model, invoke):
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


def test_candidate_runtime_and_skills_are_the_files_that_execute(tmp_path, invoke):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    tool = candidate / 'tools' / 'shell.py'
    tool.write_text(tool.read_text().replace('command = args.get("command")',
                    'command = args.get("command").replace("MINIAGENT_MOCK", "EDITED_CANDIDATE")'))
    proc, work, rows = invoke(tmp_path, harness=candidate)
    assert proc.returncode == 0, proc.stderr
    assert (work / 'answer.txt').read_text() == 'EDITED_CANDIDATE\n'
    assert any('miniagent_event' in row for row in rows)


def test_file_submission_cannot_be_replaced_by_final_chat(tmp_path, invoke):
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


def test_native_worker_refuses_ambient_dependency_overlay(tmp_path, invoke):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    overlay = tmp_path / 'work' / 'dependency-overlay'
    overlay.mkdir(parents=True)
    (overlay / 'hg_overlay_dependency.py').write_text(
        "MARKER = 'OVERLAY_DEPENDENCY_LOADED'\n", encoding='utf-8')
    tool = candidate / 'tools' / 'shell.py'
    tool.write_text(tool.read_text().replace('command = args.get("command")',
        'try:\n'
        '        from hg_overlay_dependency import MARKER\n'
        '    except ModuleNotFoundError:\n'
        '        MARKER = "ISOLATED_DEPENDENCIES"\n'
        '    command = args.get("command").replace("MINIAGENT_MOCK", MARKER)'),
        encoding='utf-8')
    proc, work, rows = invoke(tmp_path, harness=candidate,
                              env={'PYTHONPATH': str(overlay)})
    assert proc.returncode == 0, proc.stderr
    assert (work / 'answer.txt').read_text() == 'ISOLATED_DEPENDENCIES\n'


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
            'HG_AGENT_API_KEY': 'selected', 'HG_METHOD_SECRET': 'method', 'HG_ENV_API_KEY': 'env',
            'PYTHONPATH': '/overlay', 'PYTHONHOME': '/platform', 'PYTHONUSERBASE': '/user'}, tmp_path)
    assert env['HOME'] == str(tmp_path / 'home')
    assert env['HG_AGENT_API_KEY'] == 'selected'
    assert not any(key in env for key in ('OPENAI_API_KEY', 'HG_METHOD_SECRET', 'HG_ENV_API_KEY',
                                        'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE'))


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


def test_live_sdk_primary_and_side_calls_share_selected_route_and_usage(tmp_path, invoke):
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


def test_missing_provider_usage_is_unknown(tmp_path, invoke):
    with scripted_endpoint(omit_usage=True) as (base, requests):
        proc, work, rows = invoke(tmp_path, env={'HG_AGENT_BACKEND': 'openai',
            'HG_AGENT_MODEL': 'selected-model', 'HG_AGENT_BASE_URL': base})
    assert proc.returncode == 0, proc.stderr
    usage = [row['usage'] for row in rows if 'usage' in row][-1]
    assert usage == {'calls': 2, 'tokens_complete': False}


def test_context_budget_refusal_does_not_make_an_http_call(tmp_path, invoke):
    with scripted_endpoint() as (base, requests):
        proc, work, rows = invoke(tmp_path, env={'HG_AGENT_BACKEND': 'openai',
            'HG_AGENT_MODEL': 'selected-model', 'HG_AGENT_BASE_URL': base,
            'HG_AGENT_CONTEXT_WINDOW': '2049', 'HG_AGENT_MAX_OUTPUT_TOKENS': '2048'})
    assert proc.returncode != 0
    assert not requests
    assert any(row.get('error') for row in rows)


def test_timeout_keeps_error_and_usage(tmp_path, invoke):
    candidate = tmp_path / 'candidate'
    shutil.copytree(HARNESS, candidate)
    tool = candidate / 'tools' / 'shell.py'
    tool.write_text(tool.read_text().replace('command = args.get("command")', 'command = "sleep 20"'))
    proc, work, rows = invoke(tmp_path, harness=candidate, env={'HG_AGENT_MINIAGENT_TIMEOUT_S': '8'})
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
def test_native_detached_background_service_is_reaped(tmp_path, invoke):
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
    command = 'python -I -B -c ' + shlex.quote(script)
    tool.write_text(tool.read_text().replace('command = args.get("command")', f'command = {command!r}'))
    # Check the namespace PID while the adapter is still alive in that same
    # namespace. Host /proc uses different PIDs and cannot establish this fact.
    entry = candidate / 'agent.py'
    entry.write_text(entry.read_text().replace("if __name__ == '__main__':", '''
_original_run = run
def run(task, work):
    status = _original_run(task, work)
    service = int((work / 'service.pid').read_text())
    (work / 'service-reaped.txt').write_text(str(not Path(f'/proc/{service}').exists()))
    return status

if __name__ == '__main__':'''))
    proc, work, rows = invoke(tmp_path, harness=candidate)
    assert proc.returncode == 0, proc.stderr
    assert (work / 'service-reaped.txt').read_text() == 'True'


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
