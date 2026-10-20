#!/usr/bin/env python3
"""Reference letters traverse the real ordered service and isolated verifier."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets import personamem_v2 as dataset
from data.datasets.personamem_v2.lib.storage import artifact
from data.scaffolds.command_control import materialize
from eval.run import tool_relay
from eval.run.runner import run_one
from eval.verification.verifier import _verify
from eval.service.tool_transport import call
from eval.policy.runtime_policy import phase_timeout


def control_answers(tid, mode):
    gold = artifact(dataset.PREPARED_ROOT, f'gold/{tid}.json')
    return {q['id']: ('\\boxed{' + (q['correct_letter'] if mode == 'oracle'
                                 else chr(97 + (ord(q['correct_letter']) - 96) % 4)) + '}')
            for q in gold['questions']}


def run(mode, platform, tid, work, *, recordings_root=None, actor_preload=None):
    if mode not in ('oracle', 'wrong', 'noop'):
        raise ValueError('invalid control mode')
    os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
    task = dataset.control_task(tid)
    setup = copy.deepcopy(dataset.SETUP[tid])
    answers = {} if mode == 'noop' else control_answers(tid, mode)
    if platform:
        harness = work / 'harness'
        shutil.copytree(dataset.ASSETS / 'oracle_harness', harness)
        metadata = json.loads((harness / 'harness.json').read_text())
        metadata['runtime_paths'] = list(dict.fromkeys([sys.prefix, sys.base_prefix,
                                                       str(Path(sys._base_executable).parent.parent)]))
        (harness / 'harness.json').write_text(json.dumps(metadata))
        setup['files'].append({'path': '.control.json', 'content': json.dumps(
            {'mode': mode, 'answers': answers, 'actor_preload': actor_preload or {}})})
        os.environ['HG_AGENT_BACKEND'] = 'mock'
        os.environ['PATH'] = str(Path(sys.executable).resolve().parent) + os.pathsep + os.environ.get('PATH', '')
        result = run_one(harness, task, setup=setup, verifier=dataset.VERIFY[tid], env=dataset.ENV[tid],
                         sandbox=True, run_id='personamem-control-' + uuid.uuid4().hex,
                         trial_id='trial-1', run_seed='0', recordings_root=recordings_root)
        verdict = result.get('verdict') or {}
        receipt = {'verdict': verdict, 'exit_code': result.get('exit_code'), 'invalid': result.get('invalid'),
                   'stderr': result.get('stderr'), 'runtime_services': result.get('runtime_services')}
        score = verdict.get('score') if result.get('exit_code') == 0 else None
    else:
        materialize(dataset, task, work)
        relay = tool_relay.start(setup, context={'task_id': tid, 'run_id': 'personamem-direct-control-' + uuid.uuid4().hex,
                                               'trial_id': 'trial-1', 'seed': '0'})
        try:
            if mode != 'noop':
                while True:
                    item = call(relay.path, 'next', timeout_s=None, max_line_bytes=None)
                    if item['kind'] == 'done':
                        break
                    if item['kind'] == 'chunk':
                        op, params = 'ack', {'chunk_id': item['chunk_id'], 'request_id': 'a-' + item['chunk_id']}
                    else:
                        op, params = 'submit', {'question_id': item['question_id'], 'answer': answers[item['question_id']],
                                                'request_id': 's-' + item['question_id']}
                    assert call(relay.path, op, params, timeout_s=None, max_line_bytes=None).get('accepted')
            relay.stop()
            if relay.fault():
                raise RuntimeError(relay.fault())
            projection = relay.recording_input()
            shutil.copytree(projection['from'], work / projection['path'])
            if recordings_root:
                shutil.copytree(projection['from'], recordings_root)
            verifier = dataset.VERIFY[tid]
            verdict = _verify(work, verifier, '', sandbox=True,
                              timeout_s=phase_timeout(verifier.get('timeout_s'),
                                                      dataset.ENV[tid].get('verify_timeout_s')))
            score = verdict.get('score')
        finally:
            relay.cleanup()
        receipt = {'verdict': verdict, 'cleanup': {'complete': not relay.cleanup_errors, 'errors': relay.cleanup_errors}}
    return {'task_id': tid, 'mode': mode, 'platform': platform, 'score': score,
            'evidence': verdict.get('evidence') or {}, 'detail': receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    for mode in ('oracle', 'wrong', 'noop'):
        choice.add_argument('--' + mode, action='store_true')
    parser.add_argument('--platform', action='store_true')
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    mode = next(m for m in ('oracle', 'wrong', 'noop') if getattr(args, m))
    with tempfile.TemporaryDirectory(prefix='hg-personamem-control-') as directory:
        result = run(mode, args.platform, args.task_id, Path(directory),
                     recordings_root=args.output.with_suffix('.records') if args.output else None)
    if args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'task_id': args.task_id, 'mode': mode, 'platform': args.platform,
                      'score': result['score'], 'reason': result['evidence'].get('reason')}))
    if mode == 'noop':
        return 0 if result['score'] is None and result['evidence'].get('reason') == 'incomplete_episode' else 1
    return 0 if result['score'] == (1 if mode == 'oracle' else 0) else 1


if __name__ == '__main__':
    raise SystemExit(main())
