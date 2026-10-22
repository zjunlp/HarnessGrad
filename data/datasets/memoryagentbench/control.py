#!/usr/bin/env python3
"""Released-data controls: oracle, completed wrong answers, and incomplete noop."""
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
from data.datasets import memoryagentbench as dataset
from data.datasets.memoryagentbench.lib.scoring import _movie_name, _validate
from data.datasets.memoryagentbench.lib.storage import load_gold, load_entity2id
from data.scaffolds.command_control import materialize
from eval.run import tool_relay
from eval.run.runner import run_one
from eval.verification.verifier import _verify
from eval.policy.runtime_policy import phase_timeout
from eval.service.tool_transport import call

DEFAULT_TASK = 'mab-' + dataset.REVISION + '-conflict_resolution-r0000'


def control_answers(task_id, mode):
    gold = load_gold(dataset.PREPARED_ROOT, task_id)
    mapping = load_entity2id(dataset.PREPARED_ROOT)
    names = {value: _movie_name(key) for key, value in mapping.items()}
    result = {}
    for qid, answers in gold['gold_by_id'].items():
        flat = _validate(gold['source'], answers)
        if mode == 'oracle':
            answer = ('\n'.join(f'{i+1}. {names[int(value)]}' for i, value in enumerate(flat))
                      if gold['source'] == 'recsys_redial_full' else flat[0])
        else:
            answer = '' if mode == 'empty' else 'ZXQVMABWRONGSENTINEL'
        result[qid] = answer
    return result


def run(mode, platform, task_id, work, *, recordings_root=None, actor_preload=None):
    if mode not in ('oracle', 'wrong', 'empty', 'noop'):
        raise ValueError('invalid control mode')
    row = dataset._rows()[task_id]
    task = {'task_id': task_id, 'goal': dataset._instruction(row), 'inputs': []}
    answers = {} if mode == 'noop' else control_answers(task_id, mode)
    setup = copy.deepcopy(dataset.SETUP[task_id])
    if platform:
        harness = work / 'harness'
        shutil.copytree(dataset.ASSETS / 'oracle_harness', harness)
        manifest = json.loads((harness / 'harness.json').read_text())
        manifest['runtime_paths'] = list(dict.fromkeys([sys.prefix, sys.base_prefix,
                                                      str(Path(sys._base_executable).parent.parent)]))
        (harness / 'harness.json').write_text(json.dumps(manifest))
        setup['files'].append({'path': '.control.json',
                               'content': json.dumps({'mode': mode, 'answers': answers, 'actor_preload': actor_preload or {}})})
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        os.environ['HG_AGENT_BACKEND'] = 'mock'
        os.environ['PATH'] = str(Path(sys.executable).resolve().parent) + os.pathsep + os.environ.get('PATH', '')
        result = run_one(harness, task, setup=setup, verifier=dataset.VERIFY[task_id],
                         env=dataset.ENV[task_id], sandbox=True,
                         run_id='memoryagentbench-control-' + uuid.uuid4().hex[:10], run_seed='0',
                         recordings_root=recordings_root or work / 'recordings')
        verdict = result.get('verdict') or {}
        score = verdict.get('score') if result.get('exit_code') == 0 else None
        evidence = verdict.get('evidence') or {}
        receipt = {'verdict': verdict, 'invalid': result.get('invalid'),
                   'exit_code': result.get('exit_code'), 'stderr': result.get('stderr'),
                   'runtime_services': result.get('runtime_services')}
    else:
        materialize(dataset, task, work)
        relay = tool_relay.start(setup, context={'task_id': task_id, 'run_id': 'manual-control',
                                               'trial_id': 'trial-1', 'seed': '0'})
        try:
            if mode != 'noop':
                while True:
                    item = call(relay.path, 'next')
                    if item['kind'] == 'done':
                        break
                    if item['kind'] == 'chunk':
                        response = call(relay.path, 'ack', {'chunk_id': item['chunk_id'],
                                                           'request_id': 'a-' + item['chunk_id']})
                    else:
                        response = call(relay.path, 'submit', {'question_id': item['question_id'],
                            'answer': answers[item['question_id']], 'request_id': 's-' + item['question_id']})
                    assert response.get('accepted'), response
            relay.stop()
            if relay.fault():
                raise RuntimeError(relay.fault())
            projection = relay.recording_input()
            shutil.copytree(projection['from'], work / projection['path'])
            verifier = dataset.VERIFY[task_id]
            verdict = _verify(work, verifier, '', sandbox=True, timeout_s=phase_timeout(verifier.get('timeout_s'), dataset.ENV[task_id].get('verify_timeout_s')))
            evidence = verdict.get('evidence') or {}
            score = verdict.get('score')
            receipt = {'verdict': verdict, 'evidence': evidence}
        finally:
            relay.cleanup()
        receipt['cleanup'] = {'complete': not relay.cleanup_errors, 'errors': relay.cleanup_errors}
    return {'task_id': task_id, 'mode': mode, 'platform': platform,
            'score': score, 'evidence': evidence, 'detail': receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    for mode in ('oracle', 'wrong', 'empty', 'noop'):
        choice.add_argument('--' + mode, action='store_true')
    parser.add_argument('--platform', action='store_true')
    parser.add_argument('--task-id', default=DEFAULT_TASK)
    parser.add_argument('--output', type=Path, help='optional permanent control receipt outside the repository')
    args = parser.parse_args()
    mode = next(mode for mode in ('oracle', 'wrong', 'empty', 'noop') if getattr(args, mode))
    with tempfile.TemporaryDirectory(prefix='hg-memoryagentbench-control-') as directory:
        result = run(mode, args.platform, args.task_id, Path(directory),
                     recordings_root=args.output.with_suffix('.records') if args.output else None)
    if args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'task_id': args.task_id, 'mode': mode, 'platform': args.platform,
                      'score': result['score'], 'reason': result['evidence'].get('reason')}))
    if result['score'] is not None:
        print(f"reward {result['score']:g}")
        return 0 if result['score'] == (1 if mode == 'oracle' else 0) else 1
    print('CONTROL UNMEASURED: ' + result['evidence'].get('reason', 'runtime_failure'))
    return 0 if mode == 'noop' and result['evidence'].get('reason') == 'incomplete_episode' else 1


if __name__ == '__main__':
    raise SystemExit(main())
