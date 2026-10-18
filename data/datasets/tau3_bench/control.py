#!/usr/bin/env python3
"""Real official airline/8 scorer control with a deterministic local simulator transport."""
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import subprocess
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from data.datasets import tau3_bench as dataset
from eval.run.runner import run_one
from eval.run import tool_relay
from eval.service.tool_transport import call
from data.scaffolds.command_control import materialize


class Endpoint:
    """Transport fixture only. It is never part of the dataset runtime declarations."""
    def __init__(self, judge_response=None, user_tool=None):
        self.judge_response = judge_response
        self.user_tool = user_tool

    def __enter__(self):
        self.requests = []
        judge_response = self.judge_response
        user_tool = self.user_tool
        requests = self.requests
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                result = {'model': 'tau3-control-user', 'choices': [{'message': {'role': 'assistant',
                          'content': 'Please help me with my request.'}}],
                          'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
                if user_tool and len(requests) == 1 and body['model'] != 'tau3-control-judge':
                    result['choices'][0]['message'] = {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'user-action', 'type': 'function', 'function': {'name': user_tool['name'], 'arguments': json.dumps(user_tool['arguments'])}}]}
                if body['model'] == 'tau3-control-judge':
                    result['choices'][0]['message']['content'] = judge_response
                data = json.dumps(result).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.previous = {k: os.environ.get(k) for k in ('HG_ENV_MODEL', 'HG_ENV_BASE_URL', 'HG_ENV_API_KEY', 'HG_AGENT_BACKEND', 'PATH', 'HG_JUDGE_MODEL', 'HG_JUDGE_BASE_URL', 'HG_JUDGE_API_KEY')}
        os.environ.update(HG_ENV_MODEL='tau3-control-user', HG_ENV_BASE_URL=f'http://127.0.0.1:{self.server.server_port}/v1',
                          HG_ENV_API_KEY='', HG_AGENT_BACKEND='mock', PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''))
        os.environ.update(HG_JUDGE_MODEL='tau3-control-judge',
                          HG_JUDGE_BASE_URL=f'http://127.0.0.1:{self.server.server_port}/v1', HG_JUDGE_API_KEY='')
        return self

    def __exit__(self, *args):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        for key, value in self.previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


def run(oracle, platform, work, *, attack=False):
    tid = 'tau3_bench/airline/8'
    row = dataset.roster()[tid]
    config = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    actions = config['task']['evaluation_criteria']['actions']
    if any(a.get('requestor', 'assistant') != 'assistant' for a in actions):
        raise ValueError('control requires assistant-only released gold')
    task = dataset.control_task(tid)
    setup = copy.deepcopy(dataset.SETUP[tid])
    verifier = copy.deepcopy(dataset.VERIFY[tid])
    seed = str(dataset.TRIAL_PROTOCOL['seeds'][0])
    if platform:
        harness = work / 'harness'
        shutil.copytree(dataset.ASSETS / 'oracle_harness', harness)
        if attack:
            program = harness / 'agent.py'
            payload = "    for rel in ['tau3_code/lib/scoring.py', 'tau3_code/lib/numpy.py', 'tau3_transport_runtime/tool_transport.py', 'scoring.py', 'numpy.py']:\n        target = work / rel; target.parent.mkdir(parents=True, exist_ok=True)\n        target.write_text(\"raise RuntimeError('actor module imported')\\n\")\n"
            program.write_text(program.read_text().replace("    sys.path.insert(0, str(work))", payload + "    sys.path.insert(0, str(work))"))
        manifest = json.loads((harness / 'harness.json').read_text())
        manifest['runtime_paths'] = list(dict.fromkeys([sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent)]))
        (harness / 'harness.json').write_text(json.dumps(manifest))
        if oracle:
            setup['files'].append({'path': '.oracle/actions.json', 'content': json.dumps({'actions': actions})})
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        result = run_one(repo=harness, task=task, setup=setup, verifier=verifier, env=dataset.ENV[tid],
                         sandbox=True, run_id='tau3-control-' + uuid.uuid4().hex[:10], run_seed=seed)
        score = (result.get('verdict') or {}).get('score') if result.get('exit_code') == 0 else None
        return score, result
    materialize(dataset, task, work)
    relay = tool_relay.start(setup, context={'task_id': tid, 'run_id': 'control', 'trial_id': 'trial-1', 'seed': seed})
    try:
        call(relay.path, 'start', {})
        if oracle:
            for action in actions:
                call(relay.path, 'call', {'tool_name': action['name'], 'arguments': action['arguments']})
        call(relay.path, 'say', {'message': '###STOP###'})
        relay.stop()
        if relay.fault(): raise RuntimeError(relay.fault())
        record = relay.recording_input()
        shutil.copytree(record['from'], work / record['path'])
        # Manual verification uses the same private projections and scorer inputs.
        for entry in verifier['inputs']:
            dest = work / entry['path']
            if entry.get('immutable'):
                dest.symlink_to(entry['from'], target_is_directory=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry['from'], dest)
        proc = subprocess.run([sys.executable, *verifier['argv'][1:]], cwd=work, capture_output=True, text=True)
        reward = work / 'verifier/reward.txt'
        evidence = work / 'verifier/evidence.json'
        return (float(reward.read_text()) if reward.exists() else None), {'returncode': proc.returncode, 'stderr': proc.stderr, 'evidence': json.loads(evidence.read_text()) if evidence.exists() else None}
    finally:
        relay.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--oracle', action='store_true'); group.add_argument('--noop', action='store_true')
    parser.add_argument('--platform', action='store_true')
    args = parser.parse_args()
    with Endpoint(), tempfile.TemporaryDirectory(prefix='tau3-control-') as directory:
        score, detail = run(args.oracle, args.platform, Path(directory))
    print(json.dumps({'control': 'deterministic simulator transport, real official airline scorer',
                      'platform': args.platform, 'score': score, 'detail': detail}, default=str))
    print(f'reward {score:g}' if score is not None else 'CONTROL UNMEASURED')
    return 0 if score == float(args.oracle) else 1

if __name__ == '__main__':
    raise SystemExit(main())
