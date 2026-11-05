#!/usr/bin/env python3
"""Official v0.1.0 scorer control on three released tasks, one per domain.

    python3 data/datasets/tau2_bench/control.py --oracle --platform     # expect 1.0 each
    python3 data/datasets/tau2_bench/control.py --noop   --platform     # expect 0.0 each
    python3 data/datasets/tau2_bench/control.py --oracle --task-id tau2_bench/airline/8

The default three-task control retains assistant-only gold. ``--dual`` adds a separate,
explicit released telecom task: the official UserSimulator emits ``toggle_data`` as USER,
receives the actual environment response and continues before the assistant refuels data.
Both dual arms enable mobile data; only the oracle refuels, so the official ENV_ASSERTION
reward distinguishes them. This is a deterministic fixture, not a model-quality experiment.
Use ``--dual --oracle --platform --evidence-dir /tmp/new-control-evidence`` to retain the
trusted sealed episode and assertion results from an actual sandbox execution.

The second party is a **deterministic local transport fixture**, never a model: every completion
in the default three-task mode returns one fixed customer sentence, so that control proves the released scorer and the released
gold actions are reachable, not that any model behaves. The three defaults are chosen, not picked
by id, for properties the control needs:

* gold actions are **all the agent's** -- no `requestor: "user"` action, which only the simulator
  could perform (measured: 0 in airline/retail, 94 in telecom);
* the gold action set **changes state** (`book_reservation`, `exchange_delivered_order_items`,
  an escalation to a human agent), so a do-nothing episode cannot match it;
* `communicate_info` is empty, so the arm does not have to be scripted to *say* a phrase the
  scorer would look for; the DB / environment-assertion half is what is being controlled;
* `NL_ASSERTION` is **not** in the basis. `EvaluationType.ALL` still makes one judge call for a
  task with `nl_assertions`. The fixture returns one explicitly false result per assertion;
  coverage is complete but this is not a judgement. A reward-bearing NL task is refused.
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


DUAL_TASK = 'tau2_bench/telecom/[mobile_data_issue]data_mode_off|data_usage_exceeded[PERSONA:None]'
DUAL_CALL_ID = 'control-user-toggle-data'
DUAL_CONTINUATION = 'Mobile data is on. Please add two GB to line L1002.'


def fixture_message(body, dual=False):
    """Explicit local fixture, not a policy or a judge. Drive the official UserSimulator.

    Both arms enable data through the genuine USER tool. Only the oracle refuels data:
    official ENV_ASSERTION must therefore still reject the no-assistant-action arm.
    """
    if body.get('model') == 'tau2-control-judge':
        import ast
        prompt = body['messages'][-1]['content']
        assertions = ast.literal_eval(prompt.split('expectedOutcomes:', 1)[1].strip())
        if not isinstance(assertions, list) or any(not isinstance(a, str) for a in assertions):
            raise ValueError('fixture expected a list of NL assertions')
        return {'role': 'assistant', 'content': json.dumps({'results': [
            {'expectedOutcome': assertion, 'metExpectation': False,
             'reasoning': 'Deterministic fixture; not a model judgement.'}
            for assertion in assertions]})}
    if dual:
        results = [m for m in body['messages'] if m.get('role') == 'tool'
                   and m.get('tool_call_id') == DUAL_CALL_ID]
        if not results:
            return {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': DUAL_CALL_ID, 'type': 'function', 'function': {
                    'name': 'toggle_data', 'arguments': '{}'}}]}
        if len(results) != 1 or not results[0]['content'].startswith('Mobile Data is now ON.'):
            raise ValueError('dual fixture requires the real successful toggle_data response')
        return {'role': 'assistant', 'content': DUAL_CONTINUATION}
    return {'role': 'assistant', 'content': 'Please help me with my request.'}


class Endpoint:
    """Transport fixture only. It is never part of the dataset runtime declarations."""
    def __init__(self, dual=False):
        self.dual = dual

    def __enter__(self):
        self.requests = []
        requests = self.requests
        dual = self.dual
        self.errors = errors = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    requests.append(body)
                    message = fixture_message(body, dual)
                    result = {'model': body.get('model'), 'choices': [{'message': message}],
                              'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
                    data = json.dumps(result).encode()
                    self.send_response(200)
                except Exception as exc:
                    errors.append(f'{type(exc).__name__}: {exc}')
                    data = json.dumps({'error': errors[-1]}).encode()
                    self.send_response(500)
                self.send_header('Content-Length', str(len(data)))
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


def _gold_actions(tid, dual=False):
    row = dataset.roster()[tid]
    config = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    criteria = config['task']['evaluation_criteria'] or {}
    actions = criteria.get('actions') or []
    user_actions = [a for a in actions if a.get('requestor', 'assistant') != 'assistant']
    if dual:
        if tid != DUAL_TASK or [(a['name'], a['arguments'], a['requestor']) for a in user_actions] != [('toggle_data', {}, 'user')]:
            raise ValueError('dual fixture supports only the explicit released toggle_data task')
        actions = [a for a in actions if a.get('requestor', 'assistant') == 'assistant']
    elif user_actions:
        raise ValueError('control requires assistant-only released gold')
    if criteria.get('communicate_info'):
        raise ValueError('control requires a task whose scorer does not look for a phrase')
    basis = criteria.get('reward_basis') or ['DB', 'COMMUNICATE']
    if any(str(item).upper() == 'NL_ASSERTION' for item in basis):
        # The fixture's judge answer is not a judgement, so it may not carry part of the reward.
        raise ValueError('control requires a task whose reward does not depend on a judge')
    return config, actions


def checked_call(socket, operation, arguments):
    reply = call(socket, operation, arguments, timeout_s=None, max_line_bytes=None)
    if reply.get('error'):
        raise RuntimeError(f'{operation}: {reply["error"]}')
    return reply


def assert_dual_episode(record, tid):
    """Check custody binding and actual official USER call/result/continuation, not a proxy score."""
    if record.get('schema') != 'harnessgrad.interaction-record.v1' or record['binding']['task_id'] != tid:
        raise ValueError('missing trusted interaction envelope or incorrect task binding')
    payload = record['payload']
    if payload.get('failure') is not None or payload.get('num_errors') != 0:
        raise ValueError('dual episode has runtime errors')
    messages = payload['messages']
    calls = [(i, c) for i, m in enumerate(messages) if m['role'] == 'user'
             for c in m.get('tool_calls') or [] if c['id'] == DUAL_CALL_ID]
    if len(calls) != 1:
        raise ValueError('expected exactly one genuine USER toggle_data call')
    index, action = calls[0]
    if (action['name'], action['arguments'], action['requestor']) != ('toggle_data', {}, 'user'):
        raise ValueError('incorrect USER tool call')
    response = messages[index + 1]
    if not (response['role'] == 'tool' and response.get('requestor') == 'user'
            and response.get('id') == DUAL_CALL_ID and not response.get('error')
            and response['content'].startswith('Mobile Data is now ON.')):
        raise ValueError('incorrect USER tool response')
    continuation = messages[index + 2]
    if continuation['role'] != 'user' or continuation.get('content') != DUAL_CONTINUATION:
        raise ValueError('missing official UserSimulator continuation')
    return {'user_tool': 'toggle_data', 'requestor': 'user', 'response_checked': True,
            'continuation_checked': True, 'trusted_binding_checked': True}


def run(oracle, platform, work, tid, dual=False):
    config, actions = _gold_actions(tid, dual=dual)
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
        previous_root = os.environ.get('HARNESSGRAD_WORK_ROOT')
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        try:
            result = run_one(repo=harness, task=task, setup=setup, verifier=verifier,
                             env=dataset.ENV[tid], sandbox=True,
                             recordings_root=work / 'recordings',
                             run_id='tau2-control-' + uuid.uuid4().hex[:10], run_seed=seed)
        finally:
            if previous_root is None:
                os.environ.pop('HARNESSGRAD_WORK_ROOT', None)
            else:
                os.environ['HARNESSGRAD_WORK_ROOT'] = previous_root
        score = (result.get('verdict') or {}).get('score') if result.get('exit_code') == 0 else None
        if dual and score is not None:
            record = result['verdict']['interaction_record']
            result['dual_assertions'] = assert_dual_episode(json.loads(Path(record['archive']).read_text()), tid)
        return score, result
    materialize(dataset, task, work)
    relay = tool_relay.start(setup, context={'task_id': tid, 'run_id': 'control',
                                             'trial_id': 'trial-1', 'seed': seed})
    try:
        checked_call(relay.path, 'start', {})
        if oracle:
            for action in actions:
                checked_call(relay.path, 'call', {'tool_name': action['name'],
                                                  'arguments': action['arguments']})
        checked_call(relay.path, 'say', {'message': '###STOP###'})
        relay.stop()
        if relay.fault(): raise RuntimeError(relay.fault())
        record = relay.recording_input(archive_root=work / 'recordings')
        dual_assertions = assert_dual_episode(json.loads(Path(relay.recording['archive']).read_text()), tid) if dual else None
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
        return ((float(reward.read_text()) if proc.returncode == 0 and reward.exists() else None),
                {'returncode': proc.returncode, 'stderr': proc.stderr,
                 'dual_assertions': dual_assertions,
                 'evidence': json.loads(evidence.read_text()) if evidence.exists() else None})
    finally:
        relay.cleanup()


def retain_recordings(value, source, target):
    """Rebase archive references after copying evidence, retaining original custody paths."""
    if isinstance(value, dict):
        result = {key: retain_recordings(item, source, target) for key, item in value.items()}
        archive = value.get('archive')
        if isinstance(archive, str):
            try:
                relative = Path(archive).relative_to(source)
            except ValueError:
                return result
            destination = target / relative
            if not destination.is_file():
                raise ValueError('retained recording is missing: ' + str(destination))
            result['original_archive'] = archive
            result['archive'] = str(destination.resolve())
        return result
    if isinstance(value, list):
        return [retain_recordings(item, source, target) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--oracle', action='store_true')
    group.add_argument('--noop', action='store_true')
    parser.add_argument('--platform', action='store_true', help='execute the real sandbox runner')
    parser.add_argument('--dual', action='store_true', help='explicit genuine USER toggle_data fixture')
    parser.add_argument('--evidence-dir', type=Path, help='new directory retaining results and sealed episodes')
    parser.add_argument('--task-id', action='append', dest='task_ids',
                        help='repeatable; defaults to one released task per domain')
    args = parser.parse_args()
    if args.dual and args.task_ids and args.task_ids != [DUAL_TASK]:
        parser.error('--dual supports only the explicit released DUAL_TASK')
    if args.evidence_dir:
        args.evidence_dir.mkdir(parents=True, exist_ok=False)
    expected = 1.0 if args.oracle else 0.0
    verdicts = []
    with Endpoint(dual=args.dual) as endpoint, tempfile.TemporaryDirectory(prefix='tau2-control-') as directory:
        for index, tid in enumerate(args.task_ids or ((DUAL_TASK,) if args.dual else TASKS)):
            work = Path(directory) / f'task-{index}'
            work.mkdir()
            before = len(endpoint.requests)
            try:
                score, detail = run(args.oracle, args.platform, work, tid, dual=args.dual)
            except Exception as exc:
                score, detail = None, {'error': f'{type(exc).__name__}: {exc}'}
            requests = endpoint.requests[before:]
            verdicts.append({'task_id': tid, 'score': score,
                             'model_calls': len(requests),
                             'environment_calls': sum(r.get('model') == 'tau2-control-user' for r in requests),
                             'judge_calls': sum(r.get('model') == 'tau2-control-judge' for r in requests),
                             'detail': detail})
            if args.evidence_dir:
                target = args.evidence_dir / f'task-{index}'
                target.mkdir()
                if (work / 'recordings').exists():
                    shutil.copytree(work / 'recordings', target / 'recordings')
                    verdicts[-1] = retain_recordings(
                        verdicts[-1], work / 'recordings', target / 'recordings')
                (target / 'requests.json').write_text(json.dumps(requests, ensure_ascii=False))
                (target / 'result.json').write_text(json.dumps(verdicts[-1], default=str, ensure_ascii=False))
            print(f'{tid}: reward {score:g}' if score is not None
                  else f'{tid}: CONTROL UNMEASURED', flush=True)
    summary = {'control': 'deterministic simulator transport, official v0.1.0 ALL scorer',
               'platform': args.platform, 'dual': args.dual, 'oracle': args.oracle,
               'fixture_errors': endpoint.errors, 'tasks': verdicts}
    if args.evidence_dir:
        (args.evidence_dir / 'summary.json').write_text(json.dumps(summary, default=str, ensure_ascii=False))
    print(json.dumps(summary, default=str, ensure_ascii=False))
    return 0 if not endpoint.errors and all(v['score'] == expected for v in verdicts) else 1


if __name__ == '__main__':
    raise SystemExit(main())
