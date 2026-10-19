"""Network stub, real sandbox, and complete-manifest controls; no model experiment."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from eval.run import runner, repeated_trials
from eval.verification.verifier import _verify
from test_shared_dataset_runtime import harness, TRANSPORT


@pytest.fixture
def endpoint(monkeypatch):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((body,self.headers.get('Authorization')))
            if body['messages'][0]['content']=='fail':
                self.send_response(503);self.end_headers();return
            out=json.dumps({'model':'fixture-model','choices':[{'message':{'content':'safe fixture score'}}],
                            'usage':{'prompt_tokens':7,'completion_tokens':3}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(out)));self.end_headers();self.wfile.write(out)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('HG_JUDGE_BASE_URL',f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('HG_JUDGE_MODEL','fixture-model')
    monkeypatch.setenv('HG_JUDGE_API_KEY','fixture-secret')
    yield received
    server.shutdown();server.server_close();thread.join()


def judge_spec(message='ok', attempts=1):
    check=f'''import os
from pathlib import Path
from transport import call
assert 'HG_JUDGE_API_KEY' not in os.environ
for i in range({attempts}):
 try: call(os.environ['HG_JUDGE_SOCKET'],'complete',{{'messages':[{{'role':'user','content':{message!r}}}]}})
 except RuntimeError: pass
Path('verifier/reward.txt').write_text('1')
'''
    return {'kind':'command','argv':['python','check.py'],'reward_file':'/verifier/reward.txt',
            'judge':{'model':'fixture-model','max_calls':1,'max_output_tokens':16,'timeout_s':2,
                     'parameters':{'temperature':0}},
            'result_contract':{'version':1,'score_range':[0,1]},
            'inputs':[{'path':'check.py','content':check},{'path':'transport.py','from':str(TRANSPORT)}]}


def test_judge_is_verifier_only_and_has_separate_usage(tmp_path,endpoint):
    result=_verify(tmp_path,judge_spec(),'x',sandbox=True,timeout_s=10)
    assert result['score']==1,result
    assert result['judge']['usage']=={'prompt_tokens':7,'completion_tokens':3}
    assert endpoint[0][1]=='Bearer fixture-secret'
    assert endpoint[0][0]['model']=='fixture-model'
    assert endpoint[0][0]['max_tokens']==16
    assert 'fixture-secret' not in json.dumps(result)


@pytest.mark.parametrize('message,attempts',[('fail',1),('ok',2)])
def test_judge_failure_or_budget_exhaustion_cannot_be_hidden_by_scorer(tmp_path,endpoint,message,attempts):
    result=_verify(tmp_path,judge_spec(message,attempts),'',sandbox=True,timeout_s=10)
    assert result['score'] is None and result['outcome']=='unmeasured',result
    assert len(endpoint)==1


def test_judge_requires_explicit_endpoint(tmp_path,monkeypatch):
    monkeypatch.delenv('HG_JUDGE_BASE_URL',raising=False)
    result=_verify(tmp_path,judge_spec(),'',sandbox=True,timeout_s=1)
    assert result['score'] is None and 'HG_JUDGE_BASE_URL' in result['unmeasured']


PROTOCOL={'version':1,'metric':'pass^k','trials':3,'ks':[1,2,3],'seeds':[10,20,30]}

def test_complete_trial_protocol_is_wired_and_old_mean_is_preserved(tmp_path,monkeypatch):
    calls=[]
    def fake(*args,**kwargs):
        calls.append(kwargs)
        score=[1.,0.,1.][len(calls)-1]
        return {'verdict':{'kind':'command','score':score,'passed':bool(score),
                           'scorer_isolation_class':'actor-workdir'},'trace':'','exit_code':0,'stderr':''}
    monkeypatch.setattr(runner,'run_one',fake)
    result=runner._evaluate(tmp_path,[{'task_id':'a'}],{},trials=3,
        verifiers={'a':{'kind':'command','trial_protocol':PROTOCOL}},progress_dir=tmp_path/'results')
    assert result['per_task']['a']==pytest.approx(2/3)
    assert result['repeated_trials']['metrics']=={'1':pytest.approx(2/3),'2':pytest.approx(1/3),'3':0}
    assert [c['run_seed'] for c in calls]==['10','20','30']
    assert len({c['trial_id'] for c in calls})==3
    assert list((tmp_path/'results').rglob('repeated-trials.json'))


def test_incomplete_trials_do_not_publish_a_smaller_denominator(tmp_path,monkeypatch):
    n=0
    def fake(*a,**kw):
        nonlocal n
        n+=1
        if n==2:return {'invalid':{'stage':'check','detail':'fixture failure'}}
        return {'verdict':{'score':1,'passed':True,'scorer_isolation_class':'actor-workdir'},'exit_code':0,'trace':'','stderr':''}
    monkeypatch.setattr(runner,'run_one',fake)
    result=runner._evaluate(tmp_path,[{'task_id':'a'}],{},trials=3,
                           verifiers={'a':{'trial_protocol':PROTOCOL}})
    report=result['repeated_trials']
    assert not report['complete'] and report['metrics'] is None
    assert len(report['invalid'])==1 and len(report['missing'])==1


def test_manifest_rejects_duplicate_and_unexpected_trials():
    manifest=[{'task_id':'a','trial_id':'1','seed':'10'}]
    row={**manifest[0],'score':1,'status':'measured'}
    with pytest.raises(ValueError,match='duplicate'):
        repeated_trials.aggregate(manifest,[row,row],[1])
    with pytest.raises(ValueError,match='seed'):
        repeated_trials.aggregate(manifest,[{**row,'seed':'wrong'}],[1])


def test_two_workers_run_the_candidate_with_separate_state_and_mailboxes(tmp_path,monkeypatch):
    monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    repo=harness(tmp_path,'''import time,json
from transport import call
t=json.loads(Path(a.task).read_text());me=t['worker_id'];peer=next(p for p in t['peers'] if p!=me)
assert os.environ['HG_WORKER_ID']==me
call('/run/harnessgrad-tool/tool.sock','send',{'to':peer,'message':'hello from '+me})
for i in range(100):
 messages=call('/run/harnessgrad-tool/tool.sock','receive',{})
 if messages:break
 time.sleep(.02)
assert messages[0]['from']==peer
(w/'patch.diff').write_text(me+' received '+messages[0]['message'])
''')
    workers=[{'id':name,'goal':'collaborate','files':[{'path':'transport.py','from':str(TRANSPORT)}],
              'env':{'kind':'files','network':'none'},'artifacts':['patch.diff'],'timeout_s':5}
             for name in ['alice','bob']]
    check='''import json
from pathlib import Path
assert Path('workers/alice/patch.diff').read_text()=='alice received hello from bob'
assert Path('workers/bob/patch.diff').read_text()=='bob received hello from alice'
r=json.loads(Path('worker_record/workers.json').read_text())
assert len(r['events'])==2
Path('verifier/reward.txt').write_text('1')
'''
    verifier={'kind':'command','argv':['python','check.py'],'reward_file':'/verifier/reward.txt',
              'inputs':[{'path':'check.py','content':check}],
              'result_contract':{'version':1,'score_range':[0,1]}}
    result=runner.run_one(repo,{'task_id':'pair'},setup={'workers':workers},verifier=verifier)
    assert result.get('verdict',{}).get('score')==1,result
    assert len(result['worker_group']['workers'])==2
    assert result['runtime_services']['workers']['cleanup']['complete']


def test_environment_broker_uses_its_own_configuration(tmp_path,endpoint,monkeypatch):
    from eval.service.judge import Judge
    from eval.service.tool_transport import call
    import os
    for suffix in ('BASE_URL','MODEL','API_KEY'):
        monkeypatch.setenv('HG_ENV_'+suffix,os.environ['HG_JUDGE_'+suffix])
    with Judge(judge_spec()['judge'],role='environment') as broker:
        reply=call(broker.path,'complete',{'messages':[{'role':'user','content':'ok'}]})
        assert reply['content']=='safe fixture score'
    assert broker.report()['role']=='environment'
    assert broker.report()['usage']['completion_tokens']==3


def test_worker_artifacts_cannot_follow_symlinks_or_fifos(tmp_path):
    from eval.run.artifacts import collect
    import os
    (tmp_path/'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):collect(tmp_path,['link'])
    os.mkfifo(tmp_path/'pipe')
    with pytest.raises(ValueError):collect(tmp_path,['pipe'])


def test_legacy_evaluation_has_no_repeated_trial_report(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'run_one',lambda *a,**k:{'verdict':{'score':1,'passed':True,
        'scorer_isolation_class':'actor-workdir'},'exit_code':0,'trace':'','stderr':''})
    assert 'repeated_trials' not in runner._evaluate(tmp_path,[{'task_id':'t'}],{})


def test_new_protocol_does_not_pollute_legacy_measurement_cache(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'run_one',lambda *a,**k:{'verdict':{'score':1,'passed':True,
        'scorer_isolation_class':'actor-workdir'},'exit_code':0,'trace':'','stderr':''})
    cache={}
    runner._evaluate(tmp_path,[{'task_id':'t'}],{},cache=cache,trials=3,
                     verifiers={'t':{'trial_protocol':PROTOCOL}})
    assert cache=={}


def test_model_tools_are_fixed_by_declaration(endpoint):
    from eval.service.judge import Judge
    from eval.service.tool_transport import call
    spec=judge_spec()['judge']
    spec['tools']=[{'type':'function','function':{'name':'lookup','parameters':{'type':'object'}}}]
    spec['tool_choice']='auto'
    with Judge(spec) as broker:
        call(broker.path,'complete',{'messages':[{'role':'user','content':'ok'}]})
    assert endpoint[0][0]['tools']==spec['tools']
    assert endpoint[0][0]['tool_choice']=='auto'


def test_failed_worker_retains_peer_evidence_and_cleanup(tmp_path,monkeypatch):
    from eval.run import cooperative
    repo=harness(tmp_path,'pass')
    def fake(repo,task,**kwargs):
        if task['worker_id']=='a':raise RuntimeError('fixture launch failure')
        return {'exit_code':0,'stderr':'','trace':'','artifacts':{}}
    monkeypatch.setattr(runner,'run_one',fake)
    workers=[{'id':name,'goal':'fixture','artifacts':['patch.diff']} for name in ('a','b')]
    result=cooperative.run(repo,{'task_id':'t'},setup={'workers':workers},
        verifier={'kind':'command','argv':['false']},env=None,timeout_s=2,verify_timeout_s=2,
        sandbox=True,run_id='r',run_seed='s',trial_id='1',recordings_root=tmp_path/'archives')
    assert result['invalid']['stage']=='worker execution'
    assert result['worker_group']['workers']['a']['invalid']['stage']=='worker launch'
    assert result['worker_group']['workers']['b']['exit_code']==0
    assert (Path(result['worker_group']['archive'])/'workers.json').is_file()
    assert result['runtime_services']['workers']['cleanup']['complete']


@pytest.mark.parametrize('seed', [None, '', '1.5', 1.5, True])
def test_environment_trial_seed_requires_an_explicit_integer(seed):
    from eval.service.judge import Judge, JudgeUnavailable
    spec = {**judge_spec()['judge'], 'seed_source': 'trial'}
    with pytest.raises(JudgeUnavailable, match='explicit integer'):
        Judge(spec, role='environment', trial_seed=seed)


def test_trial_seed_option_is_environment_only_and_excludes_static_seed():
    from data.scaffolds.registry import _check_setup
    from data.scaffolds.runtime_extensions import validate_judge
    spec = {**judge_spec()['judge'], 'seed_source': 'trial'}
    with pytest.raises(ValueError):
        validate_judge(spec)
    validate_judge(spec, allow_trial_seed=True)
    _check_setup({'tool_relay': {'model': spec}}, 'seeded-task', 'fixture')
    with pytest.raises(ValueError, match='cannot combine'):
        validate_judge({**spec, 'parameters': {'seed': 1}}, allow_trial_seed=True)
    with pytest.raises(ValueError, match='must be trial'):
        validate_judge({**spec, 'seed_source': 'actor'}, allow_trial_seed=True)


@pytest.mark.parametrize('trial_seed', ['13', '29'])
def test_relay_model_receives_trial_seed_without_recording(tmp_path, endpoint, monkeypatch, trial_seed):
    import os
    for suffix in ('BASE_URL', 'MODEL', 'API_KEY'):
        monkeypatch.setenv('HG_ENV_' + suffix, os.environ['HG_JUDGE_' + suffix])
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    program = tmp_path / 'environment.py'
    program.write_text(f'''import argparse,json,os,signal,sys,threading
from pathlib import Path
sys.path.insert(0, {str(TRANSPORT.parent)!r})
from tool_transport import Relay,call
p=argparse.ArgumentParser()
p.add_argument('--socket');p.add_argument('--receipt');p.add_argument('--game')
a=p.parse_args();done=threading.Event()
signal.signal(signal.SIGTERM,lambda *_:done.set())
def handle(op,args):
    assert 'HG_ENV_API_KEY' not in os.environ
    return call(os.environ['HG_ENV_MODEL_SOCKET'],'complete',
                {{'messages':[{{'role':'user','content':'ok'}}]}})
with Relay(a.socket,handle) as service:
    done.wait(10)
Path(a.receipt).write_text(json.dumps(service.receipt))
''')
    spec = {**judge_spec()['judge'], 'seed_source': 'trial'}
    repo = harness(tmp_path, '''from transport import call
reply=call('/run/harnessgrad-tool/tool.sock','ask',{})
(w/'answer.txt').write_text(reply['content'])
''')
    result = runner.run_one(repo, {'task_id': 'seeded-environment'}, run_seed=trial_seed,
        setup={'files': [{'path': 'transport.py', 'from': str(TRANSPORT)}],
               'tool_relay': {'program': str(program), 'content': '{}', 'model': spec}},
        verifier={'kind': 'answer', 'expected': 'safe fixture score'})
    assert result['verdict']['passed'], result
    report = result['runtime_services']['tool_relay']
    assert report['recording'] is None
    assert report['model']['configuration']['parameters']['seed'] == int(trial_seed)
    assert endpoint[0][0]['seed'] == int(trial_seed)
    assert 'seed' not in spec['parameters']
    assert report['cleanup']['complete']


def test_environment_static_seed_is_unchanged_by_trial_seed(endpoint, monkeypatch):
    import os
    from eval.service.judge import Judge
    from eval.service.tool_transport import call
    for suffix in ('BASE_URL', 'MODEL', 'API_KEY'):
        monkeypatch.setenv('HG_ENV_' + suffix, os.environ['HG_JUDGE_' + suffix])
    spec = {**judge_spec()['judge'], 'parameters': {'seed': 7}}
    with Judge(spec, role='environment', trial_seed='99') as broker:
        call(broker.path, 'complete', {'messages': [{'role': 'user', 'content': 'ok'}]})
    assert endpoint[0][0]['seed'] == 7


def test_trial_seeded_environment_does_not_reuse_measurement_cache(tmp_path, monkeypatch):
    calls = []
    def fake(*args, **kwargs):
        calls.append(kwargs)
        return {'verdict': {'score': 1, 'passed': True, 'scorer_isolation_class': 'actor-workdir'},
                'exit_code': 0, 'trace': '', 'stderr': ''}
    monkeypatch.setattr(runner, 'run_one', fake)
    cache = {}
    for seed in ('13', '29'):
        runner._evaluate(tmp_path, [{'task_id': 't'}], {}, cache=cache, run_seed=seed,
            setups={'t': {'tool_relay': {'model': {**judge_spec()['judge'], 'seed_source': 'trial'}}}})
    assert [row['run_seed'] for row in calls] == ['13', '29']
    assert all(row.get('trial_id') for row in calls)
    assert cache == {}


@pytest.mark.parametrize('global_model', [None, 'different-global-default'])
def test_judge_uses_frozen_declaration_not_global_default(tmp_path, endpoint, monkeypatch, global_model):
    if global_model is None:
        monkeypatch.delenv('HG_JUDGE_MODEL')
    else:
        monkeypatch.setenv('HG_JUDGE_MODEL', global_model)
    result = _verify(tmp_path, judge_spec(), '', sandbox=True, timeout_s=10)
    assert result['score'] == 1, result
    assert endpoint[0][0]['model'] == 'fixture-model'
    assert result['judge']['configuration']['model'] == 'fixture-model'


def test_environment_model_mismatch_is_still_rejected(endpoint, monkeypatch):
    from eval.service.judge import Judge, JudgeUnavailable
    import os
    monkeypatch.setenv('HG_ENV_BASE_URL', os.environ['HG_JUDGE_BASE_URL'])
    monkeypatch.setenv('HG_ENV_MODEL', 'different-environment-model')
    with pytest.raises(JudgeUnavailable, match='HG_ENV_MODEL must match'):
        Judge(judge_spec()['judge'], role='environment')
