#!/usr/bin/env python3
"""Official v0.1.0 scorer control on three released tasks, one per domain.

    python3 data/datasets/tau2_bench/control.py --oracle --platform     # expect 1.0 each
    python3 data/datasets/tau2_bench/control.py --noop   --platform     # expect 0.0 each
    python3 data/datasets/tau2_bench/control.py --oracle --task-id tau2_bench/airline/8

The second party is a **deterministic local transport fixture**, never a model: every completion
returns one fixed customer sentence, so the control proves the released scorer and the released
gold actions are reachable, not that any model behaves. The three defaults are chosen, not picked
by id, for properties the control needs:

* gold actions are **all the agent's** -- no `requestor: "user"` action, which only the simulator
  could perform (measured: 0 in airline/retail, 94 in telecom);
* the gold action set **changes state** (`book_reservation`, `exchange_delivered_order_items`,
  an escalation to a human agent), so a do-nothing episode cannot match it;
* `communicate_info` is empty, so the arm does not have to be scripted to *say* a phrase the
  scorer would look for; the DB / environment-assertion half is what is being controlled;
* `NL_ASSERTION` is **not** in the basis. `EvaluationType.ALL` still makes one judge call for a
  task with `nl_assertions`, so the fixture answers it with `{"results": []}` -- which the release
  reads as "nothing to grade", multiplies to 1.0, and the basis then multiplies out. A task whose
  *reward* depended on that answer is refused here, because a fixture cannot judge.
"""
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
from data.datasets import tau2_bench as dataset
from eval.run.runner import run_one
from eval.run import tool_relay
from eval.service.tool_transport import call
from data.scaffolds.command_control import materialize
from eval.service.judge import Judge

#: One task per domain, each satisfying every property the docstring lists.
TASKS = (
    'tau2_bench/airline/8',
    'tau2_bench/retail/0',
    'tau2_bench/telecom/[service_issue]break_apn_settings|lock_sim_card_pin[PERSONA:None]',
)


class Endpoint:
    """Transport fixture only. It is never part of the dataset runtime declarations."""
    def __enter__(self):
        self.requests = []
        requests = self.requests
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                if body.get('model') == 'tau2-control-judge':
                    # A task whose basis excludes NL_ASSERTION multiplies this out; the release
                    # reads `[]` as "nothing to grade" rather than as a verdict.
                    result = {'model': 'tau2-control-judge', 'choices': [{'message': {
                              'role': 'assistant', 'content': json.dumps({'results': []})}}],
                              'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
                else:
                    result = {'model': 'tau2-control-user', 'choices': [{'message': {'role': 'assistant',
                              'content': 'Please help me with my request.'}}],
                              'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
                data = json.dumps(result).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.previous = {k: os.environ.get(k) for k in (
            'HG_ENV_MODEL', 'HG_ENV_BASE_URL', 'HG_ENV_API_KEY', 'HG_AGENT_BACKEND', 'PATH',
            'HG_JUDGE_MODEL', 'HG_JUDGE_BASE_URL', 'HG_JUDGE_API_KEY')}
        os.environ.update(HG_ENV_MODEL='tau2-control-user',
                          HG_ENV_BASE_URL=f'http://127.0.0.1:{self.server.server_port}/v1',
                          HG_ENV_API_KEY='', HG_AGENT_BACKEND='mock',
                          PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''))
        os.environ.update(HG_JUDGE_MODEL='tau2-control-judge', HG_JUDGE_API_KEY='',
                          HG_JUDGE_BASE_URL=f'http://127.0.0.1:{self.server.server_port}/v1')
        return self

    def __exit__(self, *args):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        for key, value in self.previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


def _gold_actions(tid):
    row = dataset.roster()[tid]
    config = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    criteria = config['task']['evaluation_criteria'] or {}
    actions = criteria.get('actions') or []
    if any(a.get('requestor', 'assistant') != 'assistant' for a in actions):
        raise ValueError('control requires assistant-only released gold')
    if criteria.get('communicate_info'):
        raise ValueError('control requires a task whose scorer does not look for a phrase')
    basis = criteria.get('reward_basis') or ['DB', 'COMMUNICATE']
    if any(str(item).upper() == 'NL_ASSERTION' for item in basis):
        # The fixture's judge answer is not a judgement, so it may not carry part of the reward.
        raise ValueError('control requires a task whose reward does not depend on a judge')
    return config, actions


def run(oracle, platform, work, tid):
    config, actions = _gold_actions(tid)
    task = dataset.control_task(tid)
    setup = copy.deepcopy(dataset.SETUP[tid])
    verifier = copy.deepcopy(dataset.VERIFY[tid])
    seed = str(dataset.TRIAL_PROTOCOL['seeds'][0])
    if platform:
        harness = work / 'harness'
        shutil.copytree(dataset.ASSETS / 'oracle_harness', harness)
        manifest = json.loads((harness / 'harness.json').read_text())
        manifest['runtime_paths'] = list(dict.fromkeys(
            [sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent)]))
        (harness / 'harness.json').write_text(json.dumps(manifest))
        if oracle:
            setup['files'].append({'path': '.oracle/actions.json',
                                   'content': json.dumps({'actions': actions})})
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        result = run_one(repo=harness, task=task, setup=setup, verifier=verifier,
                         env=dataset.ENV[tid], sandbox=True,
                         run_id='tau2-control-' + uuid.uuid4().hex[:10], run_seed=seed)
        score = (result.get('verdict') or {}).get('score') if result.get('exit_code') == 0 else None
        return score, result
    materialize(dataset, task, work)
    relay = tool_relay.start(setup, context={'task_id': tid, 'run_id': 'control',
                                             'trial_id': 'trial-1', 'seed': seed})
    try:
        call(relay.path, 'start', {}, timeout_s=None, max_line_bytes=None)
        if oracle:
            for action in actions:
                reply = call(relay.path, 'call', {'tool_name': action['name'],
                                                  'arguments': action['arguments']},
                             timeout_s=None, max_line_bytes=None)
                if reply.get('error'):
                    raise RuntimeError(f'{tid}: {reply["error"]}')
        call(relay.path, 'say', {'message': '###STOP###'}, timeout_s=None, max_line_bytes=None)
        relay.stop()
        if relay.fault(): raise RuntimeError(relay.fault())
        record = relay.recording_input()
        shutil.copytree(record['from'], work / record['path'])
        for entry in verifier['inputs']:
            dest = work / entry['path']
            if entry.get('immutable'):
                dest.symlink_to(entry['from'], target_is_directory=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry['from'], dest)
        environment = dict(os.environ)
        judge = Judge(verifier['judge']) if verifier.get('judge') else None
        if judge is None:
            proc = subprocess.run(verifier['argv'], cwd=work, capture_output=True, text=True,
                                  errors='replace', env=environment)
        else:
            with judge:
                environment['HG_JUDGE_SOCKET'] = str(judge.path)
                proc = subprocess.run(verifier['argv'], cwd=work, capture_output=True, text=True,
                                      errors='replace', env=environment)
        reward = work / 'verifier/reward.txt'
        evidence = work / 'verifier/evidence.json'
        return ((float(reward.read_text()) if reward.exists() else None),
                {'returncode': proc.returncode, 'stderr': proc.stderr,
                 'evidence': json.loads(evidence.read_text()) if evidence.exists() else None})
    finally:
        relay.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--oracle', action='store_true')
    group.add_argument('--noop', action='store_true')
    parser.add_argument('--platform', action='store_true')
    parser.add_argument('--task-id', action='append', dest='task_ids',
                        help='repeatable; defaults to one released task per domain')
    args = parser.parse_args()
    expected = 1.0 if args.oracle else 0.0
    verdicts = []
    with Endpoint() as endpoint, tempfile.TemporaryDirectory(prefix='tau2-control-') as directory:
        for index, tid in enumerate(args.task_ids or TASKS):
            work = Path(directory) / f'task-{index}'
            work.mkdir()
            score, detail = run(args.oracle, args.platform, work, tid)
            verdicts.append({'task_id': tid, 'score': score,
                             'model_calls': len(endpoint.requests), 'detail': detail})
            print(f'{tid}: reward {score:g}' if score is not None
                  else f'{tid}: CONTROL UNMEASURED', flush=True)
    print(json.dumps({'control': 'deterministic simulator transport, official v0.1.0 ALL scorer',
                      'platform': args.platform, 'oracle': args.oracle,
                      'tasks': verdicts}, default=str, ensure_ascii=False))
    return 0 if all(v['score'] == expected for v in verdicts) else 1


if __name__ == '__main__':
    raise SystemExit(main())
