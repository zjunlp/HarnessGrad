"""Offline reuse/resume controls: preserve evidence without running models again."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from harnessgrad import verification_context
from harnessgrad.orchestration.historical_evidence import reused_measurement


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def _records(tmp_path):
    point = {'round': 0, 'identity': {'harness_sha': 'a' * 40},
             'per_task': {'family/one': .5, 'family/two': 0},
             'verification_context': verification_context.create(
                 {'family/one': 'b' * 64, 'family/two': 'c' * 64}),
             'harness_failed': {'family/one': {'reason': 'authored failure'}},
             'timeouts': {'family/two': {'reason': 'authored timeout'}}}
    _write(tmp_path / 'round0_traces/family/one.jsonl', 'original trace\n')
    _write(tmp_path / 'round0_traces/family/one.trial1.jsonl', 'original trial\n')
    _write(tmp_path / 'round0_traces/private-eval.jsonl', 'PRIVATE SENTINEL')
    _write(tmp_path / 'verdicts/round-0/family/one.json', json.dumps({
        'task_id': 'family/one', 'round': 0, 'score': .5, 'kind': 'native',
        'native_runtime': {'verified': True, 'runtime_id': 'authored'},
        'evidence': {'trial': {'index': 0}}, 'harness': {'exit_code': 1},
        'trials': [{'kind': 'native', 'native_runtime': {'verified': True}}],
        'trial_scores': [None]}))
    return point


def test_reuse_restores_nested_traces_native_receipts_and_missing_status(tmp_path):
    point = _records(tmp_path)
    restored = reused_measurement(tmp_path, point, [point])
    assert restored['traces'] == {'family/one': 'original trace\n'}
    assert restored['trial_traces'] == {'family/one': ['original trial\n']}
    assert restored['verdicts']['family/one']['native_runtime']['verified'] is True
    assert restored['trial_verdicts']['family/one'][0]['score'] is None
    assert restored['harness_output']['family/one']['exit_code'] == 1
    assert restored['harness_failed'] == point['harness_failed']
    assert restored['timeouts'] == point['timeouts']
    assert restored['reused_evidence'] == {'source_round': 0,
        'missing_trace_tasks': ['family/two'], 'missing_verdict_tasks': ['family/two']}
    assert 'PRIVATE SENTINEL' not in str(restored)


def test_reused_alias_restores_original_receipts_not_empty_alias_files(tmp_path):
    point = _records(tmp_path)
    alias = {**point, 'round': 2, 'reused_from': 0}
    _write(tmp_path / 'verdicts/round-2/family/one.json',
           json.dumps({'task_id': 'family/one', 'score': .5}))
    restored = reused_measurement(tmp_path, alias, [point, alias])
    assert restored['reused_evidence']['source_round'] == 0
    assert restored['verdicts']['family/one']['native_runtime']['verified'] is True


@pytest.mark.parametrize('corruption', ['cycle', 'missing', 'source', 'score', 'context'])
def test_invalid_reuse_lineage_is_rejected(tmp_path, corruption):
    point = _records(tmp_path)
    alias = copy.deepcopy({**point, 'round': 2, 'reused_from': 0})
    if corruption == 'cycle': alias['reused_from'] = 2
    elif corruption == 'missing': alias['reused_from'] = 1
    elif corruption == 'source': alias['identity']['harness_sha'] = 'd' * 40
    elif corruption == 'score': alias['per_task']['family/one'] = 1
    else: alias['verification_context'] = verification_context.create(
        {'family/one': 'e' * 64, 'family/two': 'c' * 64})
    with pytest.raises(ValueError):
        reused_measurement(tmp_path, alias, [point, alias])


ACTOR = '''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
w=Path(a.workdir);w.mkdir(parents=True,exist_ok=True)
(w/'answer.txt').write_text('4')
(w/'trace.jsonl').write_text(json.dumps({'authored_source':'initial'})+'\\n'+json.dumps({'usage':{'input_tokens':0,'output_tokens':0,'calls':0}})+'\\n')
'''

METHOD = '''import json,shutil,sys
from pathlib import Path
sys.path.insert(0, METHODS)
import _siblings
protocol=_siblings.load('protocol');req=protocol.read_request();protocol.require_api(req)
base=Path(req['base_harness']);dest=Path(req['workspace'])/'candidate'
assert "'initial'" in (Path(req['pristine_harness'])/'agent.py').read_text()
shutil.copytree(base,dest,ignore=shutil.ignore_patterns('.git','_harnessgrad'))
n=req['round_index']
if n==1:
    p=dest/'agent.py';p.write_text(p.read_text().replace("'initial'","'updated'"))
else:
    trace=base/'_harnessgrad/traces/t01.jsonl'
    assert json.loads(trace.read_text().splitlines()[0])['authored_source']=='updated'
step={'harness_dir':str(dest),'label':'authored continuing control'}
if DECLARED and n>1:step.update(decision='no_change',incumbent_round=n-1)
Path(req['trajectory_out']).write_text(json.dumps({'steps':[step],'trajectory_shape':'sequence'}))
protocol.emit({'steps':1,'changed':True,'generation_tokens':0})
'''


@pytest.mark.parametrize('declared', [False, True])
def test_three_round_reuse_and_resume_stage_latest_source_evidence(tmp_path, declared):
    root = Path(__file__).resolve().parents[2]
    harness = tmp_path / 'harness'; harness.mkdir()
    (harness / 'harness.json').write_text(json.dumps({'name': 'authored-reuse', 'version': '1',
        'entrypoint': 'agent.py', 'backend': 'cli', 'env_kinds': ['files']}))
    (harness / 'agent.py').write_text(ACTOR)
    method = tmp_path / 'method.py'
    method.write_text('METHODS='+repr(str(root / 'methods'))+'\nDECLARED='+repr(declared)+'\n'+METHOD)
    argv = [sys.executable, 'driver.py', '--harness-path', str(harness), '--dataset', 'demo',
            '--tasks', 't01', '--sampling', 'all', '--improver', 'none', '--no-sandbox',
            '--jobs', '1', '--run-id', 'reuse', '--runs-root', str(tmp_path / 'runs'),
            '--work-root', str(tmp_path / 'work'), '--method-entrypoint', f'{sys.executable} {method}']
    if not declared: argv.append('--reuse-identical')
    env = {**os.environ, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'HG_AGENT_BACKEND': 'mock',
           'HG_AGENT_MODEL': 'authored-no-model'}
    for rounds, extra in [('3', []), ('4', ['--resume'])]:
        result = subprocess.run([*argv, '--rounds', rounds, *extra], cwd=root, env=env,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
        if rounds == '3':
            path = tmp_path / 'runs/reuse/verdicts/round-0/t01.json'
            record = json.loads(path.read_text())
            record['evidence'] = {'authored_receipt': 'retain across resume'}
            path.write_text(json.dumps(record))
    run = tmp_path / 'runs/reuse'
    points = [json.loads(line) for line in (run / 'curve.jsonl').read_text().splitlines()]
    assert [point['round'] for point in points] == [0, 1, 2, 3, 4]
    assert points[0]['identity']['harness_sha'] != points[1]['identity']['harness_sha']
    assert json.loads((run/'verdicts/round-0/t01.json').read_text())['evidence'] == {
        'authored_receipt': 'retain across resume'}
    for point in points[2:]:
        assert point['per_task'] == points[1]['per_task']
        assert point['reused_evidence']['source_round'] == 1
        assert point['cost']['evaluation_trials'] == 0
        assert json.loads((run/f"round{point['round']}_traces/t01.jsonl").read_text().splitlines()[0]) == {
            'authored_source': 'updated'}
