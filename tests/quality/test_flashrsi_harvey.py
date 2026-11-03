"""Harvey native file/dual-judge controls; no paid endpoints or fake grader modules.

Native execution tests require a separately provisioned pinned lab-core runtime.
The localhost providers return scripted verdicts to the real upstream SDKs and
rubric evaluator; these are protocol controls, not benchmark model results.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from data.scaffolds.flashrsi_research import harvey_native as native
from eval.run.runner import harness_env
from harnessgrad.config.areas import ConfigMissing

PYTHON=Path(os.environ.get('HG_HARVEY_TEST_PYTHON','/tmp/hg-harvey-native-venv/bin/python'))
PANDOC=Path(os.environ.get('HG_HARVEY_TEST_PANDOC','/tmp/hg-harvey-native-venv/lib/python3.12/site-packages/pypandoc/files/pandoc'))
NATIVE_READY=PYTHON.is_file()and PANDOC.is_file()
needs_runtime=pytest.mark.skipif(not NATIVE_READY,reason='pinned lab-core/Anthropic/pandoc runtime is not provisioned')


def basic_env():
    return {'HG_JUDGE_HARVEY_PYTHON':str(sys.executable),
        'HG_JUDGE_HARVEY_PANDOC':str(sys.executable),
        'HG_JUDGE_HARVEY_ANTHROPIC_API_KEY':'ant-private-control',
        'HG_JUDGE_HARVEY_OPENAI_API_KEY':'oai-private-control',
        'HG_JUDGE_HARVEY_ANTHROPIC_BASE_URL':'http://127.0.0.1:40001',
        'HG_JUDGE_HARVEY_OPENAI_BASE_URL':'http://127.0.0.1:40002/v1'}


def test_all_provider_credentials_are_hidden_from_actor(monkeypatch):
    for k,v in basic_env().items():monkeypatch.setenv(k,v)
    actor=harness_env()
    assert not any(name.startswith('HG_JUDGE_HARVEY_')for name in actor)


def test_absent_dual_provider_is_conditional_missing():
    env=basic_env();env.pop('HG_JUDGE_HARVEY_ANTHROPIC_API_KEY')
    with pytest.raises(ConfigMissing,match='configured independently'):native.settings(env)


def test_no_official_paid_default_or_public_endpoint():
    env=basic_env();env.pop('HG_JUDGE_HARVEY_OPENAI_BASE_URL')
    with pytest.raises(ConfigMissing,match='paid endpoint defaults are disabled'):native.settings(env)
    env=basic_env();env['HG_JUDGE_HARVEY_OPENAI_BASE_URL']='https://api.openai.com/v1'
    with pytest.raises(ConfigMissing):native.settings(env)


def test_file_paths_and_dual_models_are_not_actor_choices():
    with pytest.raises(ValueError):native._task_path('../rubric')
    with pytest.raises(ValueError):native._task_path('/tmp/private')
    env=basic_env();env['HG_JUDGE_HARVEY_ANTHROPIC_MODEL']='gpt-not-anthropic'
    with pytest.raises(ValueError,match='native provider identity'):native.settings(env)
    assert native.export_paths({}, {})==['output']


@contextmanager
def local_providers(*, anthropic_pass=True,openai_pass=True,malformed_openai=False,incomplete_openai=False):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path':self.path,'body':body,'key':self.headers.get('Authorization')or self.headers.get('x-api-key')})
            verdict='pass'if(anthropic_pass if self.path.endswith('/messages')else openai_pass)else'fail'
            text=('NOT_A_VERDICT' if malformed_openai and not self.path.endswith('/messages')
                  else json.dumps({'reasoning':'local scripted control','verdict':verdict}))
            if self.path.endswith('/messages'):
                message={'id':'msg-control','type':'message','role':'assistant','model':body['model'],'content':[],
                    'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':7,'output_tokens':0}}
                events=[('message_start',{'type':'message_start','message':message}),
                    ('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}),
                    ('content_block_delta',{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':text}}),
                    ('content_block_stop',{'type':'content_block_stop','index':0}),
                    ('message_delta',{'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':4}}),
                    ('message_stop',{'type':'message_stop'})]
                raw=''.join(f'event: {kind}\ndata: {json.dumps(event)}\n\n'for kind,event in events).encode()
                ctype='text/event-stream'
            else:
                payload={'id':'resp-control','object':'response','created_at':0,'status':'incomplete' if incomplete_openai else 'completed','model':body['model'],
                    'output':[{'type':'message','id':'out-control','role':'assistant','status':'completed',
                        'content':[{'type':'output_text','text':text,'annotations':[]}]}],
                    'usage':{'input_tokens':7,'output_tokens':4,'total_tokens':11}}
                raw=json.dumps(payload).encode();ctype='application/json'
            self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',requests
    finally:server.shutdown();server.server_close();thread.join()


def configure(monkeypatch,base):
    for key,value in basic_env().items():monkeypatch.setenv(key,value)
    monkeypatch.setenv('HG_JUDGE_HARVEY_PYTHON',str(PYTHON))
    monkeypatch.setenv('HG_JUDGE_HARVEY_PANDOC',str(PANDOC))
    monkeypatch.setenv('HG_JUDGE_HARVEY_ANTHROPIC_BASE_URL',base)
    monkeypatch.setenv('HG_JUDGE_HARVEY_OPENAI_BASE_URL',base+'/v1')
    monkeypatch.setenv('HG_JUDGE_HARVEY_ANTHROPIC_MODEL','claude-local-control')
    monkeypatch.setenv('HG_JUDGE_HARVEY_OPENAI_MODEL','gpt-local-control')
    monkeypatch.setenv('HG_JUDGE_HARVEY_PARALLEL','1')


def private_export(tmp_path):
    root=tmp_path/'export';root.mkdir();(root/'output').mkdir()
    (root/'output/deliverable.md').write_text('A native file work product.\n')
    tid='harvey_lab/control/native-case'
    task={'title':'Authored local protocol control','instructions':'Produce a short deliverable.',
        'criteria':[{'id':'criterion','title':'Private local criterion',
            'match_criteria':'PRIVATE_NATIVE_RUBRIC_MARKER','deliverables':['deliverable.md']}]}
    case={'task_id':tid,'native_task_id':'control/native-case','row':{'task':task},'question':task['instructions']}
    raw=json.dumps(case).encode();(root/'flashrsi_case.json').write_bytes(raw)
    spec={'native_benchmark':{'dataset':'harvey_lab'},
        'scorer_input':{'task_id':tid,'sha256':hashlib.sha256(raw).hexdigest()}}
    return root,tid,spec


@needs_runtime
@pytest.mark.parametrize('verdicts,expected',[((True,True),1.0),((True,False),.5),((False,False),0.0)])
def test_real_pinned_evaluator_runs_both_native_provider_sdks(tmp_path,monkeypatch,verdicts,expected):
    root,tid,spec=private_export(tmp_path)
    with local_providers(anthropic_pass=verdicts[0],openai_pass=verdicts[1])as(base,requests):
        configure(monkeypatch,base)
        runtime=native.readiness()
        assert runtime['lab_core']=='1.2.0'and runtime['anthropic']=='0.88.0'
        result=native.run(spec,root,task_id=tid,run_id='local-control',trial_id='0',timeout_s=30)
    assert result['score']==expected,result
    assert result['evidence']['metrics']['all_pass_rate']==expected
    assert {r['path']for r in requests}=={'/v1/messages','/v1/responses'}
    assert len(requests)==2
    assert 'PRIVATE_NATIVE_RUBRIC_MARKER' in json.dumps([r['body']for r in requests])
    assert 'PRIVATE_NATIVE_RUBRIC_MARKER' not in json.dumps(result)
    assert 'ant-private-control'not in json.dumps(result)and'oai-private-control'not in json.dumps(result)
    assert result['evidence']['native_runtime']['judge_profile']=='custom-dual'


@needs_runtime
@pytest.mark.parametrize('failure',['malformed','incomplete'])
def test_native_dual_provider_parse_failure_remains_unmeasured(tmp_path,monkeypatch,failure):
    root,tid,spec=private_export(tmp_path)
    with local_providers(malformed_openai=failure=='malformed',incomplete_openai=failure=='incomplete')as(base,requests):
        configure(monkeypatch,base)
        result=native.run(spec,root,task_id=tid,run_id='failed-native-control',trial_id='0',timeout_s=30)
    assert result['score'] is None and result['outcome']=='unmeasured',result
    assert result['evidence']['measured'] is False
    assert result['evidence']['error_type']=='RuntimeError'
    assert {r['path']for r in requests}=={'/v1/messages','/v1/responses'}
    assert 'PRIVATE_NATIVE_RUBRIC_MARKER'not in json.dumps(result)


@needs_runtime
@pytest.mark.parametrize('providers,expected',[((True,True),1.0),((False,False),0.0)])
def test_real_hg_actor_files_private_native_dual_verifier(tmp_path,monkeypatch,providers,expected):
    from data.scaffolds.flashrsi_research.adapter import Adapter
    from tools.prepare_flashrsi_research import prepare
    from eval.ckpt.git_state import stage_workspace
    from eval.run.runner import run_one
    task_root=tmp_path/'native/tasks/control/document-product';task_root.mkdir(parents=True)
    (task_root/'documents').mkdir()
    (task_root/'documents/public.md').write_text('PUBLIC_MATTER_DOCUMENT_MARKER\n')
    task={'title':'Authored native-shape file control','instructions':'Read documents and write output/deliverable.md.',
        'criteria':[{'id':'criterion','title':'Private local criterion',
            'match_criteria':'PRIVATE_RUBRIC_NEVER_FOR_ACTOR','deliverables':['deliverable.md']}]}
    (task_root/'task.json').write_text(json.dumps(task))
    prepared=tmp_path/'prepared'
    prepare('harvey_lab',prepared,{'data_path':str(task_root)},fetch=False)
    monkeypatch.setenv('HG_HARVEY_LAB_DATA',str(prepared))
    source=tmp_path/'actor';source.mkdir()
    (source/'harness.json').write_text(json.dumps({'name':'harvey-file-transport-control','version':'1','entrypoint':'agent.py','env_kinds':['files'],'role':'control'}))
    (source/'agent.py').write_text('''import os,json
from pathlib import Path
w=Path.cwd()
assert (w/'documents/public.md').read_text().strip()=='PUBLIC_MATTER_DOCUMENT_MARKER'
assert not (w/'flashrsi_case.json').exists()
assert not any(k.startswith('HG_JUDGE_HARVEY_')for k in os.environ)
assert not Path(PREPARED).exists()
assert (w/'output').is_dir()
(w/'output/deliverable.md').write_text('A produced file work product.\\n')
(w/'answer.txt').write_text('An irrelevant final chat message.')
(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})+'\\n')
'''.replace('PREPARED',repr(str(prepared))))
    work=tmp_path/'work';repo=work/'run/workspace';stage_workspace(source,repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(work));monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    with local_providers(anthropic_pass=providers[0],openai_pass=providers[1])as(base,requests):
        configure(monkeypatch,base)
        adapter=Adapter('harvey_lab');task_spec=adapter.load()[0][0]
        result=run_one(repo,task_spec,sandbox=True,setup=adapter.setup(task_spec['task_id']),
            env=adapter.environment(task_spec['task_id']),verifier=adapter.verifier(task_spec['task_id']),
            timeout_s=30,verify_timeout_s=30,run_id='harvey-native-file-control',trial_id='0')
    assert not result.get('invalid'),result
    assert result['exit_code']==0,result.get('stderr')
    assert result['verdict']['score']==expected,result['verdict']
    assert result['verdict']['evidence']['measured'] is True
    assert {r['path']for r in requests}=={'/v1/messages','/v1/responses'}
    assert 'PRIVATE_RUBRIC_NEVER_FOR_ACTOR'not in result['trace']
    assert 'A produced file work product.'in json.dumps([r['body']for r in requests])
    evidence=json.dumps(result['verdict']['evidence'])
    assert 'ant-private-control'not in evidence and'oai-private-control'not in evidence
