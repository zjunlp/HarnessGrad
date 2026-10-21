#!/usr/bin/env python3
"""Official static gold/noop controls, directly and through the real sandbox."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from data.datasets import costbench as dataset
from data.scaffolds.command_control import materialize
from eval.run import tool_relay
from eval.run.runner import run_one
from eval.service.tool_transport import call


def run(oracle, platform, task_id, work, *, attack=False, recordings_root=None):
    task = dataset.control_task(task_id)
    setup = copy.deepcopy(dataset.SETUP[task_id])
    verifier = copy.deepcopy(dataset.VERIFY[task_id])
    row = dataset.roster()[task_id]
    config = json.loads((dataset.PREPARED / row['path'] / 'private.json').read_text())
    script = {'actions': config['oracle_actions'], 'answer': config['oracle_answer']}
    seed = '42'
    if platform:
        harness = work / 'harness'
        shutil.copytree(dataset.ASSETS / 'oracle_harness', harness)
        manifest_path = harness / 'harness.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['runtime_paths'] = list(dict.fromkeys([sys.prefix, sys.base_prefix,
                                                       str(Path(sys._base_executable).parent.parent)]))
        manifest_path.write_text(json.dumps(manifest))
        if oracle:
            setup['files'].append({'path': '.oracle/actions.json', 'content': json.dumps(script)})
        if attack:
            agent = harness / 'agent.py'
            attack_code = """    assert not Path(PRIVATE).exists()
    assert not (work / 'trusted_episode').exists()
    for filename in ['costbench_code/lib/scoring.py', 'costbench_bundle/env/utils/eval.py', 'scoring.py', 'yaml.py']:
        target = work / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("raise RuntimeError('actor scorer imported')\\n")
    fake = work / 'trusted_episode/episode.json'
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text('{"forged": true}')
""".replace('PRIVATE', repr(str(dataset.PREPARED / row['path'] / 'private.json')))
            agent.write_text(agent.read_text().replace('    sys.path.insert(0, str(work))',
                                                       attack_code + '    sys.path.insert(0, str(work))'))
        previous = os.environ.get('HARNESSGRAD_WORK_ROOT')
        previous_path = os.environ.get('PATH')
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + (previous_path or '')
        try:
            result = run_one(repo=harness, task=task, setup=setup, verifier=verifier,
                             env=dataset.ENV[task_id], sandbox=True,
                             run_id='costbench-control-' + uuid.uuid4().hex[:10], run_seed=seed,
                             recordings_root=recordings_root)
        finally:
            if previous is None:
                os.environ.pop('HARNESSGRAD_WORK_ROOT', None)
            else:
                os.environ['HARNESSGRAD_WORK_ROOT'] = previous
            if previous_path is None:
                os.environ.pop('PATH', None)
            else:
                os.environ['PATH'] = previous_path
        score = (result.get('verdict') or {}).get('score') if result.get('exit_code') == 0 else None
        return score, result
    materialize(dataset, task, work)
    relay = tool_relay.start(setup, context={'task_id': task_id, 'run_id': 'control',
                                           'trial_id': 'trial-1', 'seed': seed})
    try:
        if oracle:
            for action in script['actions']:
                call(relay.path, 'turn', {'content': None, 'calls': [action]},
                     timeout_s=None, max_line_bytes=None)
            call(relay.path, 'turn', {'content': '<answer> ' + script['answer'] + ' </answer>', 'calls': []},
                 timeout_s=None, max_line_bytes=None)
        relay.stop()
        if relay.fault():
            raise RuntimeError(relay.fault())
        record = relay.recording_input()
        shutil.copytree(record['from'], work / record['path'])
        for entry in verifier['inputs']:
            dest = work / entry['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            if entry.get('immutable'):
                dest.symlink_to(entry['from'], target_is_directory=True)
            else:
                shutil.copyfile(entry['from'], dest)
        proc = subprocess.run(verifier['argv'], cwd=work, capture_output=True, text=True)
        reward, evidence = work / 'verifier/reward.txt', work / 'verifier/evidence.json'
        return (float(reward.read_text()) if reward.exists() else None), {
            'returncode': proc.returncode, 'stderr': proc.stderr,
            'evidence': json.loads(evidence.read_text()) if evidence.exists() else None}
    finally:
        relay.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arm = parser.add_mutually_exclusive_group(required=True)
    arm.add_argument('--oracle', action='store_true')
    arm.add_argument('--noop', action='store_true')
    parser.add_argument('--platform', action='store_true')
    parser.add_argument('--task-id', default='costbench/test/Query00001')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    os.environ['HG_AGENT_BACKEND'] = 'mock'
    with tempfile.TemporaryDirectory(prefix='costbench-control-') as directory:
        score, detail = run(args.oracle, args.platform, args.task_id, Path(directory))
    result = {'task_id': args.task_id, 'oracle': args.oracle, 'platform': args.platform,
              'score': score, 'detail': detail}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, default=str) + '\n')
    print(json.dumps(result, ensure_ascii=False, default=str))
    print(f'reward {score:g}' if score is not None else 'CONTROL UNMEASURED')
    return 0 if score == float(args.oracle) else 1


if __name__ == '__main__':
    raise SystemExit(main())
