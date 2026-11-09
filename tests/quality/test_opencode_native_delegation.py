"""Optional native OpenCode control against a scripted local model endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_native_foreground_subagent_and_usage(tmp_path):
    source = os.environ.get('HG_OPENCODE_TEST_HARNESS')
    if not source:
        pytest.skip('set HG_OPENCODE_TEST_HARNESS to a prepared OpenCode copy')
    harness = tmp_path / 'harness'
    shutil.copytree(source, harness)
    shutil.copy2(ROOT / 'base_harness/opencode/agent.py', harness / 'agent.py')

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            self.server.requests.append(request)
            messages = request.get('messages') or []
            last = messages[-1] if messages else {}
            tools = [(t.get('function') or {}).get('name') for t in request.get('tools', [])]
            if not tools:
                text = 'SYNTHETIC_TITLE'
            elif last.get('role') == 'tool':
                text = 'PARENT_COMPLETE'
            elif 'SYNTHETIC_CHILD' in str(last.get('content', '')):
                text = 'CHILD_COMPLETE'
            else:
                text = None

            def chunk(delta, reason=None):
                payload = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 0,
                           'model': request.get('model', 'mock'),
                           'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
                return 'data: ' + json.dumps(payload) + '\n\n'

            output = chunk({'role': 'assistant'})
            if text is None:
                tool = 'subagent' if 'subagent' in tools else 'task'
                arguments = json.dumps({('agent' if tool == 'subagent' else 'subagent_type'): 'general',
                                        'description': 'Synthetic delegation',
                                        'prompt': 'SYNTHETIC_CHILD: return CHILD_COMPLETE.'})
                output += chunk({'tool_calls': [{'index': 0, 'id': 'call_child', 'type': 'function',
                    'function': {'name': tool, 'arguments': arguments}}]}) + chunk({}, 'tool_calls')
            else:
                output += chunk({'content': text}) + chunk({}, 'stop')
            output += 'data: ' + json.dumps({'id': 'test', 'object': 'chat.completion.chunk', 'created': 0,
                'model': request.get('model', 'mock'), 'choices': [],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}) + '\n\n'
            output += 'data: [DONE]\n\n'
            payload = output.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    work = tmp_path / 'work'; work.mkdir()
    task = tmp_path / 'task.json'
    task.write_text(json.dumps({'task_id': 'fixture', 'goal': 'Delegate a synthetic subtask.'}))
    env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(tmp_path),
           'HG_AGENT_BACKEND': 'openai', 'HG_AGENT_MODEL': 'synthetic-model',
           'HG_AGENT_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
           'HG_AGENT_API_KEY': 'synthetic-key', 'HG_AGENT_OPENCODE_TIMEOUT_S': '60'}
    try:
        result = subprocess.run([sys.executable, str(harness / 'agent.py'), '--task', str(task),
                                 '--workdir', str(work)], cwd=work, env=env, text=True,
                                capture_output=True, timeout=80)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    assert result.returncode == 0, result.stderr[-1200:]
    records = [json.loads(line) for line in (work / 'trace.jsonl').read_text().splitlines()]
    assert any(row.get('tool') in ('subagent', 'task') and row.get('tool_status') == 'completed' for row in records)
    assert any('opencode_subagent_session' in row for row in records)
    usage = next(row['usage'] for row in records if 'usage' in row)
    profile = json.loads((harness / 'harness.json').read_text())['_opencode_runtime']['profile']
    if profile == '1':
        assert usage['scope'] == 'subagent_usage_incomplete' and usage['input_tokens'] is None
    else:
        assert usage['scope'] == 'session_tree_including_auxiliary'
        assert usage['input_tokens'] == len(server.requests) * 10
    assert usage['subagent_sessions'] == 1
    assert (work / 'answer.txt').read_text() == 'PARENT_COMPLETE'
