"""Native WorkBench controls, never a model evaluation or a fake src implementation.

Set HG_WORKBENCH_TEST_SOURCE to an acquired checkout at the recorded pin. The
source is external to git; these tests skip when it has not been provisioned.
The controls use released reference actions solely to validate HG's instrument.
"""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from data.scaffolds.native_research.adapter import Adapter
from data.scaffolds.native_research.workbench_process import Session
from eval.ckpt.git_state import stage_workspace
from eval.run.runner import run_one
from tools.prepare_native_research import prepare

PIN='49c7dfd00c03d384ec59ea57374f50b766aa5613'
INVENTORY=json.loads((ROOT/'tests/fixtures/upstream_workbench/source.json').read_text())


@pytest.fixture(scope='module')
def native_source():
    configured = os.environ.get('HG_WORKBENCH_TEST_SOURCE')
    if not configured:
        pytest.skip('HG_WORKBENCH_TEST_SOURCE is not configured')
    source = Path(configured).expanduser().absolute()
    if not (source / 'src/evals/actions.py').is_file():
        pytest.skip('HG_WORKBENCH_TEST_SOURCE must name the pinned WorkBench checkout')
    return source


def require_pinned_source(source):
    assert INVENTORY['commit']==PIN
    for relative,expected in INVENTORY['files'].items():
        path=source/relative
        assert path.is_file() and not path.is_symlink(),relative
        assert hashlib.sha256(path.read_bytes()).hexdigest()==expected,relative


def native_action(action):
    call=ast.parse(action,mode='eval').body
    assert isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='func'
    function=call.func.value
    assert isinstance(function,ast.Attribute) and isinstance(function.value,ast.Name)
    return {'operation':'call','name':function.value.id+'.'+function.attr,
            'arguments':{kw.arg:ast.literal_eval(kw.value)for kw in call.keywords}}


def test_native_session_real_actions_grade_and_reset(native_source):
    # Published first calendar task: delete the first event on December 13.
    require_pinned_source(native_source)
    action='calendar.delete_event.func(event_id="00000256")'
    first=Session(native_source)
    try:
        assert 'calendar.delete_event' in first.description
        observed=first.request(native_action(action))
        assert observed=={'result':'Event deleted successfully.'}
        graded=first.request({'operation':'grade','reference':[action]})
        assert graded['metrics']=={'correct':1.0,'exact_match':1.0,'unwanted_side_effects':0.0}
    finally:first.close()
    fresh=Session(native_source)
    try:
        graded=fresh.request({'operation':'grade','reference':[action]})
        assert graded['metrics']['correct']==0.0
    finally:fresh.close()


@pytest.fixture(scope='module')
def prepared_native(tmp_path_factory,native_source):
    require_pinned_source(native_source)
    target=tmp_path_factory.mktemp('workbench-prepared')/'prepared'
    manifest=prepare('workbench',target,{'data_path':str(native_source/'data'),
        '_prepared_root':str(native_source),'domains':['calendar']},fetch=False)
    assert len(manifest['records'])==110
    return target,manifest


@pytest.mark.parametrize('oracle',[True,False])
def test_native_tool_relay_post_exit_state_verifier(oracle,prepared_native,tmp_path,monkeypatch):
    prepared,manifest=prepared_native
    monkeypatch.setenv('HG_DATA_WORKBENCH',str(prepared))
    adapter=Adapter('workbench')
    task=adapter.load()[0][0]
    case=adapter.case(manifest['records'][0],manifest)
    reference=ast.literal_eval(case['row']['outcome'])
    expected=[native_action(action)for action in reference] if oracle else []
    source=tmp_path/'control';source.mkdir()
    (source/'harness.json').write_text(json.dumps({'name':'workbench-native-oracle-control'if oracle else'workbench-native-noop-control','version':'1','entrypoint':'agent.py','env_kinds':['files'],'role':'control'}))
    actor='''import json,os,subprocess,sys
from pathlib import Path
w=Path.cwd()
assert not (w/'upstream_case.json').exists()
assert not (w/'workbench_world').exists()
assert not (w/'trusted_episode').exists()
assert not Path(PRIVATE_DATA_PATH).exists()
trace=[]
result=subprocess.run([sys.executable,'workbench.py','describe'],capture_output=True,text=True,errors='replace')
assert result.returncode==0,result.stderr
assert 'calendar.delete_event' in result.stdout
for action in ACTIONS:
    result=subprocess.run([sys.executable,'workbench.py','call',action['name'],json.dumps(action['arguments'])],capture_output=True,text=True,errors='replace')
    assert result.returncode==0,result.stderr
    trace.append({'command':action['name'],'observation':{'kind':'api','returncode':result.returncode,'output':result.stdout}})
(w/'answer.txt').write_text('This chat answer is deliberately irrelevant to the native world.')
trace.append({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})
(w/'trace.jsonl').write_text(''.join(json.dumps(row)+'\\n'for row in trace))
'''.replace('PRIVATE_DATA_PATH',repr(str(prepared))).replace('ACTIONS',repr(expected))
    (source/'agent.py').write_text(actor)
    work=tmp_path/'work';repo=work/'run/workspace';stage_workspace(source,repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(work));monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    result=run_one(repo,task,sandbox=True,setup=adapter.setup(task['task_id']),
                   env=adapter.environment(task['task_id']),verifier=adapter.verifier(task['task_id']),
                   timeout_s=30,verify_timeout_s=30,run_id='native-workbench-'+str(oracle),trial_id='0')
    assert not result.get('invalid'),json.dumps(result,default=str)
    assert result['exit_code']==0,result.get('stderr')
    verdict=result['verdict']
    assert verdict['score']==float(oracle),json.dumps(verdict,default=str)
    assert verdict['evidence']['measured'] is True
    assert verdict['evidence']['metrics']['correct']==float(oracle)
    assert verdict['evidence']['metrics']['unwanted_side_effects']==0
    assert not verdict.get('unmeasured')
    assert case['row']['outcome'] not in result['trace']
    assert 'PRIVATE' not in json.dumps(verdict['evidence'])
