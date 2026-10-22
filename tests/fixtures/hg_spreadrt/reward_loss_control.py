"""Synthetic fault injection at reward transport, after the real authored scorer returns."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import openpyxl
from data.scaffolds.scorer_input import bind
from eval.verification import verifier

work = Path('/mnt/20t/xubuqiang/hg-work/spreadrt') / ('reward-loss-' + uuid.uuid4().hex)
work.mkdir()
wb = openpyxl.Workbook()
wb.active.title = 'Synthetic'
wb.active['A1'] = 5
wb.save(work / 'source.xlsx')
wb.save(work / 'result.xlsx')
wb.close()
spec = {'kind': 'command', 'argv': ['python3', 'check.py', '--workdir', '.',
        '--reward-file', 'verifier/reward.txt', '--evidence-file', 'verifier/evidence.json'],
        'inputs': [{'path': 'check.py', 'from': str(ROOT / 'data/datasets/spreadsheetbench/verify/check.py')},
                   {'path': 'golden.xlsx', 'from': str(work / 'source.xlsx')},
                   {'path': 'task.json', 'content': json.dumps({'task_id': 'authored-reward-loss',
                    'answer_position': 'Synthetic!A1', 'instruction_type': 'synthetic'})}],
        'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json'}
spec = bind(spec, 'authored-reward-loss', ['golden.xlsx', 'task.json'])
run = subprocess.run


def lose_reward(argv, **kwargs):
    proc = run(argv, **kwargs)
    print('real_scorer_argv=' + json.dumps(argv))
    print('real_scorer_exit=' + str(proc.returncode))
    print('real_scorer_stdout=' + repr(proc.stdout))
    print('real_scorer_stderr=' + repr(proc.stderr))
    assert proc.returncode == 0
    reward = work / 'verifier/reward.txt'
    print('reward_before_injected_loss=' + repr(reward.read_text()))
    reward.unlink()
    print('fault_injected=unlink authored reward after successful real scorer process')
    return proc


print('label=synthetic reward-transport fault injection; no dataset execution')
verifier.subprocess.run = lose_reward
try:
    verdict = verifier._verify(work, spec, '', sandbox=False, timeout_s=30,
                               env={'kind': 'files'}, task_id='authored-reward-loss')
finally:
    verifier.subprocess.run = run
print('verdict=' + json.dumps(verdict, sort_keys=True))
assert verdict['score'] == 0.0 and 'unmeasured' not in verdict
assert verdict['evidence']['scorer_input']['verified'] is True
assert verdict['evidence']['score'] == 1.0 and verdict['evidence']['measured'] is True
print('infrastructure_failure_as_zero=YES; matching bound evidence survives while reward disappears')
shutil.rmtree(work)
print('synthetic_work_removed=' + str(not work.exists()))
