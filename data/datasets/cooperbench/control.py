#!/usr/bin/env python3
"""Model-free released oracle/noop, with the real isolated image verifier."""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets import cooperbench as dataset
from eval.run.runner import run_one
from eval.verification.verifier import _verify


def run_control(task_id, oracle, platform, root):
    task = dataset.control_task(task_id)
    verifier = dataset.VERIFY[task_id]
    root.mkdir(parents=True, exist_ok=True)
    if platform:
        candidate = root / 'candidate'
        candidate.mkdir()
        (candidate / 'harness.json').write_text(json.dumps({'entrypoint': 'agent.py'}))
        patch = next(iter(dataset.oracle_files(task).values())) if oracle else None
        (candidate / 'agent.py').write_text('''import argparse,json,os,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
w=Path(a.workdir);sys.path.insert(0,str(w))
import cooperbench
assert not (w/'private').exists() and not (w/'worker_record').exists()
assert not (w/'workers').exists()
assert os.getuid()==65534
assert 'HG_JUDGE_API_KEY' not in os.environ and 'HG_ENV_API_KEY' not in os.environ
peer='agent2' if os.environ['HG_WORKER_ID']=='agent1' else 'agent1'
cooperbench.call('send',{'to':peer,'message':'control-ready'})
for _ in range(500):
 messages=cooperbench.call('receive',{})
 if messages:break
 time.sleep(.01)
assert messages and messages[0]['from']==peer
''' + (f"(w/'agent.patch').write_text({patch!r})\n" if oracle else '') +
            "(w/'trace.jsonl').write_text(json.dumps({'usage':{'input_tokens':0,'output_tokens':0,'calls':0}})+'\\n')\n")
        os.environ['HG_AGENT_BACKEND'] = 'mock'
        return run_one(candidate, task, setup=deepcopy(dataset.SETUP[task_id]), verifier=verifier,
                       env=dataset.ENV[task_id], run_id='cooperbench-control', trial_id='oracle' if oracle else 'noop',
                       recordings_root=root / 'recordings', timeout_s=60, verify_timeout_s=1800)
    work = root / 'verify'
    work.mkdir()
    if oracle:
        for rel, content in dataset.oracle_files(task).items():
            path = work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    return {'verdict': _verify(work, verifier, '', sandbox=True, env={'kind': 'files'}, timeout_s=1800,
                               task_id=task_id, run_id='cooperbench-control', trial_id='oracle' if oracle else 'noop')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arm = parser.add_mutually_exclusive_group(required=True)
    arm.add_argument('--oracle', action='store_true')
    arm.add_argument('--noop', action='store_true')
    parser.add_argument('--platform', action='store_true')
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='hg-cooperbench-control-'))
    try:
        result = run_control(args.task_id, args.oracle, args.platform, root)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2))
        score = result.get('verdict', {}).get('score')
        print(json.dumps({'dataset': 'cooperbench', 'mode': 'oracle' if args.oracle else 'noop',
                          'platform': args.platform, 'reward': score, 'result': result}))
        print(f'reward {score:g}' if score is not None else 'CONTROL UNMEASURED: no score')
        return 0 if score == int(args.oracle) else 1
    finally:
        # run_one has already stopped all owned containers and channels here.
        if not args.output:
            shutil.rmtree(root)


if __name__ == '__main__':
    raise SystemExit(main())
