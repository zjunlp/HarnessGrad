"""Release entrypoints fail before H0; a surviving H0 is not run completion."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
from types import SimpleNamespace
from types import ModuleType

import pytest

from harnessgrad import config, readiness
from harnessgrad.completion import describe
from harnessgrad.experiments import to_argv

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(('HG_METHOD_', 'HG_IMPROVER', 'HG_AGENT_', 'HG_JUDGE_', 'HG_ENV_')) or name == 'DEEPSEEK_API_KEY':
            monkeypatch.delenv(name)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    # These tests isolate configuration; missing SDK admission has its own regression.
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *a, **kw:
        object() if name == 'openai' else original_find_spec(name, *a, **kw))
    config.reset()
    yield
    config.reset()


def args(name='noop', **changes):
    values = dict(side='train', rounds=1, sampling='all', trials=1, harness='loop',
        harness_dir=str(ROOT/'base_harness/loop'), improver=None, improver_model=None,
        method_entrypoint=shlex.join([sys.executable, str(ROOT/'methods'/name/'run.py')]))
    values.update(changes)
    return SimpleNamespace(**values)


def endpoint_env(monkeypatch, prefix, base):
    for field, value in [('BASE_URL',base), ('MODEL','local-scripted'), ('API_KEY','PRIVATE_CONTROL_KEY')]:
        monkeypatch.setenv(prefix+field,value)
    config.reset()


def test_live_agent_configuration_is_checked_locally_and_redacted(monkeypatch):
    monkeypatch.setenv('HG_AGENT_BACKEND','openai');config.reset()
    rows = readiness.configuration_checks(args())
    assert rows[0].name == 'agent' and not rows[0].ok and rows[0].required
    assert 'HG_AGENT_MODEL' in rows[0].detail and 'HG_AGENT_API_KEY' in rows[0].detail
    endpoint_env(monkeypatch,'HG_AGENT_','http://127.0.0.1:1/v1')
    assert readiness.configuration_checks(args())[0].ok
    assert 'PRIVATE_CONTROL_KEY' not in readiness.render(readiness.configuration_checks(args()))


def test_known_published_method_requires_editor_before_h0():
    rows = readiness.configuration_checks(args('dgm'))
    assert len(rows)==1 and rows[0].name=='method' and not rows[0].ok
    assert 'HG_METHOD_' in rows[0].detail


def test_local_configuration_check_never_connects(monkeypatch):
    endpoint_env(monkeypatch,'HG_METHOD_','http://127.0.0.1:1/v1')
    def forbidden(*_args,**_kwargs): raise AssertionError('local preflight contacted a provider')
    monkeypatch.setattr(readiness.urllib.request,'urlopen',forbidden)
    assert readiness.configuration_checks(args('dgm'))[-1].ok


@pytest.mark.parametrize('name', ['noop','echo_base'])
def test_model_free_and_external_methods_are_not_assumed_to_need_editor(name,tmp_path):
    assert readiness.configuration_checks(args(name))==[]
    script=tmp_path/'own.py';script.write_text('pass\n')
    assert readiness.configuration_checks(args(method_entrypoint=shlex.join([sys.executable, str(script)])))==[]
    assert readiness.configuration_checks(args('dgm',side='eval'))==[]
    assert readiness.configuration_checks(args('dgm',rounds=0))==[]


def test_instruction_module_and_trial_requirements_are_local(monkeypatch):
    endpoint_env(monkeypatch,'HG_METHOD_','http://127.0.0.1:1/v1')
    rows=readiness.configuration_checks(args('opro'))
    assert not rows[-1].ok and 'instruction_path' in rows[-1].detail
    assert readiness.configuration_checks(args('opro',harness='miniagent',
        harness_dir=str(ROOT/'base_harness/miniagent')))[-1].ok
    rows=readiness.configuration_checks(args('modular_rsi'))
    assert not rows[-1].ok
    monkeypatch.setenv('HG_POLICY_CONFIG',json.dumps({'modules':{'core':['agent.py']}}))
    rows=readiness.configuration_checks(args('modular_rsi',trials=2))
    assert rows[-1].ok
    monkeypatch.setenv('HG_POLICY_CONFIG',json.dumps({'modules':{'core':['../private']}}))
    assert not readiness.configuration_checks(args('modular_rsi',trials=2))[-1].ok


@contextmanager
def server(status=200):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            calls.append((self.command,self.path));raw=b'{"data":[]}'
            self.send_response(status);self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            calls.append(('POST',self.path));self.send_error(500,'generation forbidden')
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{http.server_port}/v1',calls
    finally:http.shutdown();http.server_close();thread.join()


def test_requested_network_checks_each_needed_channel_and_never_generate(monkeypatch):
    with server() as (agent,calls_a),server(503) as (method,calls_m):
        monkeypatch.setenv('HG_AGENT_BACKEND','openai')
        endpoint_env(monkeypatch,'HG_AGENT_',agent);endpoint_env(monkeypatch,'HG_METHOD_',method)
        rows=readiness.endpoint_checks(args('dgm'))
        assert [r.name for r in rows]==['agent-endpoint','method-endpoint']
        assert rows[0].ok and not rows[1].ok and all(r.required for r in rows)
        assert calls_a==calls_m==[('GET','/v1/models')]
        assert 'PRIVATE_CONTROL_KEY' not in readiness.render(rows)
        assert not readiness.all_required_ok(rows)


def test_malformed_endpoint_url_never_echoes_private_value(monkeypatch,tmp_path):
    marker='PRIVATE_URL_MARKER_RELEASE_CONTROL'
    result=readiness._probe_endpoint('agent-endpoint','invalid:'+marker,'private-key')
    assert not result.ok and result.required
    assert marker not in readiness.render([result])
    env=clean_env();env.update(HG_AGENT_BACKEND='openai',HG_AGENT_MODEL='local-scripted',
        HG_AGENT_BASE_URL=marker,HG_AGENT_API_KEY='private-key')
    completed=driver(tmp_path,rounds=0,extra=('--check-net',),env=env)
    assert completed.returncode==2
    assert marker not in completed.stdout+completed.stderr
    assert 'Traceback' not in completed.stderr


@pytest.mark.parametrize('role,prefix',[('judge','HG_JUDGE_'),('environment','HG_ENV_')])
def test_selected_judge_and_environment_configuration_uses_no_key_placeholder(role,prefix,monkeypatch):
    spec={'model':'local-scripted','max_calls':1}
    tables={'verifiers':{'chosen':{'judge':spec}} if role=='judge' else {},
        'setups':{'chosen':{'tool_relay':{'model':spec}}} if role=='environment' else {}}
    rows=readiness.selected_model_checks(args(),**tables)
    assert len(rows)==1 and not rows[0].ok and prefix+'BASE_URL' in rows[0].detail
    with server() as (base,calls):
        monkeypatch.setenv(prefix+'BASE_URL',base)
        if role=='environment': monkeypatch.setenv(prefix+'MODEL','local-scripted')
        # Ordinary Judge admits an absent key for a local unauthenticated server.
        config.reset()
        rows=readiness.selected_model_checks(args(check_net=True),**tables)
        assert all(row.ok and row.required for row in rows)
        assert [row.name for row in rows]==[role,role+'-endpoint']
        assert calls==[('GET','/v1/models')]


@pytest.fixture
def selected_judged_dataset(monkeypatch):
    from collections.abc import Mapping
    module=ModuleType('data.demo')
    module.KIND='fixture';module.ORACLE_SOURCE='released'
    tasks=[{'task_id':name,'goal':'Write a public answer.'} for name in ('chosen','unavailable','exam')]
    module.load=lambda:(tasks,{t['task_id']:'expected' for t in tasks})
    module.SPLIT={'train':['chosen','unavailable'],'eval':['exam']}
    reads=[]
    class Selected(Mapping):
        def __init__(self,values):self.values=values
        def __iter__(self):return iter(('chosen','unavailable','exam'))
        def __len__(self):return 3
        def __getitem__(self,key):
            reads.append(key)
            if key!='chosen':raise RuntimeError('unselected model/runtime is unavailable')
            return self.values
    module.SETUP=Selected({'files':[]})
    module.VERIFY=Selected({'kind':'command','argv':['python3','grader.py'],
        'reward_file':'/verifier/reward.txt',
        'judge':{'model':'local-scripted','max_calls':1},
        'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}})
    module.ENV=Selected({'kind':'files'})
    monkeypatch.setitem(sys.modules,'data.demo',module)
    return module,reads


def test_readonly_preflight_only_loads_selected_model_declarations(selected_judged_dataset,monkeypatch):
    _,reads=selected_judged_dataset
    with server() as (base,calls):
        monkeypatch.setenv('HG_JUDGE_BASE_URL',base);config.reset()
        rows=readiness.selected_model_checks(args(dataset='demo',tasks='chosen',split='declared',check_net=True))
        assert all(row.ok for row in rows)
        assert reads==['chosen','chosen']
        assert calls==[('GET','/v1/models')]


def test_missing_selected_judge_blocks_driver_before_actor_without_curve(selected_judged_dataset,tmp_path,monkeypatch):
    import driver as entry
    _,reads=selected_judged_dataset
    monkeypatch.setattr(entry,'load_env',lambda *_:{})
    monkeypatch.setattr(entry,'_probe_tool_calls',lambda *_args,**_kwargs:pytest.fail('must not call a model'))
    monkeypatch.setattr(entry,'evaluate',lambda *_args,**_kwargs:pytest.fail('actor must not run before judge configuration'))
    monkeypatch.setattr(sys,'argv',['driver.py','--dataset','demo','--harness','loop',
        '--tasks','chosen','--sampling','all','--rounds','0','--improver','none',
        '--run-id','judge-missing','--runs-root',str(tmp_path/'runs'),'--work-root',str(tmp_path/'work')])
    assert entry.main()==2
    assert reads==['chosen','chosen','chosen']
    assert not (tmp_path/'runs').exists()
    assert not (tmp_path/'work').exists()


def test_whole_pool_status_failure_does_not_override_selected_source_ready(selected_judged_dataset,tmp_path,monkeypatch):
    module,reads=selected_judged_dataset
    module.KIND='dataset'
    module.provision_status=lambda:{'present':False,'source_prepared':True,
        'detail':'other instance runtime not provisioned'}
    monkeypatch.setattr(readiness.datasets,'published',lambda:('demo',))
    monkeypatch.setenv('HG_JUDGE_BASE_URL','http://127.0.0.1:1/v1');config.reset()
    plan=args(dataset='demo',tasks='chosen',split='declared',rounds=0,sandbox=False,
        runs_root=str(tmp_path/'runs'),work_root=str(tmp_path/'work'),check_net=False)
    rows=readiness.plan_checks(plan)
    dataset=next(row for row in rows if row.name=='dataset')
    assert dataset.ok and 'source loaded' in dataset.detail
    assert readiness.all_required_ok(rows),rows
    assert reads==['chosen','chosen']
    # A full-pool status implementation can probe unavailable unselected images.
    # Run readiness must not call it, even merely to print an informational hint.
    def full_pool_probe():raise AssertionError('whole-pool runtime probe is forbidden')
    module.provision_status=full_pool_probe
    reads.clear()
    rows=readiness.plan_checks(plan)
    assert readiness.all_required_ok(rows),rows
    assert reads==['chosen','chosen']
    # A genuinely unavailable selected runtime must still fail the same preflight.
    plan.tasks='unavailable'
    rows=readiness.plan_checks(plan)
    assert not readiness.all_required_ok(rows)
    assert any(row.name=='selected-models' and not row.ok for row in rows)
    assert not (tmp_path/'runs').exists() and not (tmp_path/'work').exists()


def clean_env():
    env={k:v for k,v in os.environ.items() if not k.startswith(('HG_METHOD_','HG_IMPROVER','HG_AGENT_'))
        and k!='DEEPSEEK_API_KEY'}
    env.update(HG_AGENT_BACKEND='mock',PYTHONDONTWRITEBYTECODE='1')
    return env


def driver(tmp_path,method='noop',run_id='control',extra=(),env=None,rounds=2):
    method_path=ROOT/'methods'/method/'run.py' if isinstance(method,str) else method
    return subprocess.run([sys.executable,'driver.py','--dataset','demo','--harness','loop',
        '--sampling','all','--rounds',str(rounds),'--method-entrypoint',shlex.join([sys.executable, str(method_path)]),
        '--improver','none','--runs-root',str(tmp_path/'runs'),'--work-root',str(tmp_path/'work'),
        '--run-id',run_id,*extra],cwd=ROOT,env=env or clean_env(),capture_output=True,text=True,timeout=90)


def test_published_missing_editor_refuses_before_any_paid_baseline(tmp_path):
    result=driver(tmp_path,method='dgm',extra=('--fail-on-incomplete',))
    assert result.returncode==2,result.stdout+result.stderr
    assert 'before H0' in result.stderr
    assert not list((tmp_path/'runs').rglob('curve.jsonl'))
    assert not (tmp_path/'work').exists()


def test_check_only_bad_endpoint_is_required_failure_and_leaves_no_run(tmp_path):
    with server(401) as (base,calls):
        env=clean_env();env.update(HG_AGENT_BACKEND='openai',HG_AGENT_MODEL='local-scripted',
            HG_AGENT_BASE_URL=base,HG_AGENT_API_KEY='PRIVATE_CONTROL_KEY')
        result=driver(tmp_path,rounds=0,extra=('--check-only','--check-net'),env=env)
        assert result.returncode==2,result.stdout+result.stderr
        assert 'agent-endpoint' in result.stdout and 'FAIL' in result.stdout
        assert 'PRIVATE_CONTROL_KEY' not in result.stdout+result.stderr
        assert calls==[('GET','/v1/models')]
        assert not (tmp_path/'runs').exists()


@pytest.mark.parametrize('strict',[False,True])
def test_method_runtime_failure_keeps_h0_but_reports_incomplete(tmp_path,strict):
    failed=tmp_path/'failed.py';failed.write_text('raise SystemExit(9)\n')
    result=driver(tmp_path,method=failed,rounds=1,extra=('--fail-on-incomplete',) if strict else ())
    assert result.returncode==(2 if strict else 0),result.stdout+result.stderr
    meta=json.loads((tmp_path/'runs/control/run_meta.json').read_text())
    assert meta['completion']['status']=='incomplete'
    assert meta['completion']['completed_rounds']==0
    assert meta['completion']['requested_rounds']==1
    assert meta['completion']['failed_rounds']==[1]
    points=[json.loads(line) for line in (tmp_path/'runs/control/curve.jsonl').read_text().splitlines()]
    assert [p['round'] for p in points]==[0]
    assert 'INCOMPLETE: 0/1' in result.stdout


@pytest.mark.parametrize('method,expected',[('noop','early_stop'),('echo_base','complete')])
def test_graceful_stop_and_completed_measurements_are_distinct_successes(tmp_path,method,expected):
    result=driver(tmp_path,method=method,extra=('--fail-on-incomplete',))
    assert result.returncode==0,result.stdout+result.stderr
    meta=json.loads((tmp_path/'runs/control/run_meta.json').read_text())
    assert meta['completion']['status']==expected
    assert meta['completion']['completed_rounds']==(1 if method=='noop' else 2)
    assert meta['completion']['coverage_complete'] is True


def test_partial_or_unmeasured_results_are_incomplete_and_retries_can_recover():
    h0={'round':0,'measured_by_platform':True,'score':0.,'n_scored':1}
    step={'round':1,'measured_by_platform':True,'score':0.,'n_scored':1}
    assert describe([h0,step],requested_rounds=1,failed_steps=[{'round':1}])['status']=='complete'
    assert describe([h0,{**step,'invalid':{'task':'environment failure'}}],requested_rounds=1)['status']=='incomplete'
    assert describe([{**h0,'n_scored':0}],requested_rounds=0)['status']=='incomplete'
    assert to_argv({'fail_on_incomplete':True})==['--fail-on-incomplete']
    assert to_argv({'fail_on_incomplete':False})==[]


def test_h0_only_smoke_does_not_demand_or_start_an_optimizer(tmp_path):
    result = subprocess.run([sys.executable, 'driver.py', '--dataset', 'demo', '--harness', 'loop',
        '--sampling', 'all', '--rounds', '0', '--improver', 'none', '--fail-on-incomplete',
        '--runs-root', str(tmp_path/'runs'), '--work-root', str(tmp_path/'work'), '--run-id', 'control'],
        cwd=ROOT, env=clean_env(), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'needs --method-entrypoint' not in result.stderr
    meta = json.loads((tmp_path/'runs/control/run_meta.json').read_text())
    assert meta['completion']['status'] == 'complete'
    assert meta['completion']['completed_rounds'] == 0
