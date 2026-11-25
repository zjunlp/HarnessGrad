#!/usr/bin/env python3
"""Run one no-model RoboCasa365 smoke through the standard HarnessGrad runner."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.datasets import robocasa365 as dataset
from eval.run.runner import run_one


ACTOR = '''#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--task')
parser.add_argument('--workdir', type=Path, required=True)
args = parser.parse_args()
work = args.workdir
assert not (work / 'robocasa365_case.json').exists()
assert not (work / 'trusted_episode').exists()
client = work / 'robocasa365_client.py'
observation = json.loads(subprocess.check_output(
    [sys.executable, str(client), 'observe', '--image-dir', 'views'], cwd=work))
assert observation['step'] == 0 and observation['instruction']
assert len(observation['state']) == 5 and len(observation['images']) == 3
for name in observation['images'].values():
    assert (work / name).read_bytes().startswith(bytes.fromhex('89504e470d0a1a0a'))
zero = {name: [0] * size for name, size in json.loads(
    (work / 'robocasa365_controls.json').read_text())['action_shapes'].items()}
(work / 'action.json').write_text(json.dumps(zero))
stepped = json.loads(subprocess.check_output(
    [sys.executable, str(client), 'step', 'action.json'], cwd=work))
assert stepped['step'] == 1 and not stepped['ended'] and 'success' not in stepped
(work / 'trace.jsonl').write_text(json.dumps({
    'harness_identity': {'harness': 'robocasa365-smoke', 'agent_model': 'mock'},
    'usage': {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}}) + '\\n')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', default='robocasa365/leaderboard/NavigateKitchen/00')
    args = parser.parse_args()
    task = {'task_id': args.task_id, 'goal': dataset.GOAL, 'inputs': []}
    setup, verifier = dataset.SETUP[args.task_id], dataset.VERIFY[args.task_id]
    os.environ['HG_AGENT_BACKEND'] = 'mock'
    os.environ['PATH'] = f'{Path(sys.executable).parent}{os.pathsep}{os.environ.get("PATH", "")}'
    with tempfile.TemporaryDirectory(prefix='hg-robocasa365-smoke-') as directory:
        work = Path(directory)
        harness = work / 'harness'
        harness.mkdir()
        actor = harness / 'agent.py'
        actor.write_text(ACTOR)
        actor.chmod(0o700)
        manifest = {
            'name': 'robocasa365-smoke', 'version': '1', 'entrypoint': 'agent.py',
            'backend': 'cli', 'env_kinds': ['files'], 'install': None, 'role': 'control',
            'runtime_paths': list(dict.fromkeys((sys.prefix, sys.base_prefix))),
        }
        (harness / 'harness.json').write_text(json.dumps(manifest))
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        result = run_one(repo=harness, task=task, setup=setup, verifier=verifier,
                         env=dataset.DEFAULT_ENV, sandbox=True, timeout_s=90,
                         run_id='robocasa365-smoke', run_seed='0')
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if (result.get('exit_code') == 0
                 and (result.get('verdict') or {}).get('score') == 0) else 1


if __name__ == '__main__':
    raise SystemExit(main())
