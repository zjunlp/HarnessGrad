"""Real task-owned HTTP proxy controls, no Trial/Docker/model evaluation."""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest
from data.scaffolds import native_harbor as native
from data.scaffolds.native_harbor_runtime import judge_gateway,python_runtime,verify
from eval.service import modelgate


@contextmanager
def fake_judge(mode='success'):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path':self.path,'model':body['model'],
                             'judge_auth_only':self.headers.get('Authorization')=='Bearer authored-judge-key'})
            if mode=='timeout':time.sleep(0.2)
            raw=json.dumps({'ok':mode!='reject'}).encode()
            self.send_response(403 if mode=='reject' else 200)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close');self.end_headers()
            try:self.wfile.write(raw)
            except OSError:pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)+'/authored/v1',requests
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


def closed(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=0.2):return False
    except OSError:return True


def test_real_proxy_path_and_exact_listener_cleanup():
    with fake_judge() as (base,requests):
        gateway=judge_gateway.open_gateway(base,'127.0.0.1');port=gateway.port
        try:
            assert gateway.bind_host=='127.0.0.1' and gateway.path=='/authored/v1'
            request=urllib.request.Request(gateway.base_url+'/chat/completions',
                data=json.dumps({'model':'authored-judge'}).encode(),headers={'Authorization':'Bearer authored-judge-key'})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=2) as response:
                assert response.status==200
        finally:gateway.stop()
        assert requests==[{'path':'/authored/v1/chat/completions','model':'authored-judge','judge_auth_only':True}]
        assert gateway._server is None and gateway._thread is None and closed(port)


@pytest.mark.parametrize('base,bridge',[
 ('https://127.0.0.1/v1','127.0.0.1'),('http://192.0.2.1/v1','127.0.0.1'),
 ('http://user:secret@127.0.0.1/v1','127.0.0.1'),('http://127.0.0.1/v1?key=secret','127.0.0.1'),
 ('http://127.0.0.1/v1','0.0.0.0')])
def test_nonlocal_secret_or_wildcard_routes_are_refused(base,bridge):
    with pytest.raises(ValueError):judge_gateway.open_gateway(base,bridge)


@pytest.mark.parametrize('mode',['success','reject','timeout','child-exception'])
def test_real_native_wrapper_proxy_and_failure_cleanup(tmp_path,monkeypatch,mode):
    store=os.environ.get('HG_TEST_NATIVE_HARBOR_RUNTIME_ROOT')
    if not store:pytest.skip('explicit real Harbor execution artifact required')
    binding=python_runtime.resolve(root=store)
    source=tmp_path/'task';source.mkdir()
    (source/'task.toml').write_text('[environment]\ndocker_image="unused"\n[verifier]\nnetwork_mode="public"\n')
    (source/'instruction.md').write_text('Authored proxy transport control')
    task_id='jobbench/authored';run_id='owned-run';trial_id='owned-trial'
    case={'dataset':'jobbench','task_id':task_id,'directory':str(source),'source_sha256':native._tree_identity(source),
          'runtime':{'harbor_python':binding['python'],'harbor_runtime':binding,'actor':{'workdir':'/workspace'},
                     'replay_actor_image':'sha256:'+'a'*64,'verifier_image':'sha256:'+'b'*64}}
    raw=json.dumps(case,sort_keys=True,separators=(',',':'));sha=hashlib.sha256(raw.encode()).hexdigest()
    root=tmp_path/'private';root.mkdir();(root/'case.json').write_text(raw)
    record=root/'native_episode';record.mkdir()
    (record/'episode.json').write_text(json.dumps({'binding':{'task_id':task_id,'run_id':run_id,'trial_id':trial_id},
        'payload':{'dataset':'jobbench','task_id':task_id,'source_sha256':case['source_sha256'],'case_sha256':sha,
                   'finished':True,'failure':None,'actor_runtime':{'cleanup':{'complete':True}},'artifacts':{}}}))
    spec={'python_runtime':binding,'native_benchmark':{'dataset':'jobbench','case_path':'case.json','case_sha256':sha}}
    audit=tmp_path/'child-audit.json';worker=tmp_path/'http-only-child.py'
    worker.write_text('''import json,sys,urllib.request
from pathlib import Path
request=json.loads(Path(sys.argv[1]).read_text());env=request['verifier_env'];status=None;error=None
try:
    call=urllib.request.Request(env['JUDGE_API_BASE']+'/chat/completions',
        data=json.dumps({'model':env['JUDGE_MODEL']}).encode(),headers={'Authorization':'Bearer '+env['JUDGE_API_KEY']})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(call,timeout=TIMEOUT) as response:status=response.status
    if __CONTROL_MODE__=='child-exception':raise RuntimeError('authored child exception')
except Exception as exc:
    status=getattr(exc,'code',status);error=type(exc).__name__
Path(AUDIT).write_text(json.dumps({'http_status':status,'error_type':error,'actor_keys_present':any(k.startswith(('HG_AGENT_','HG_ENV_','HG_METHOD_')) for k in __import__('os').environ)}))
if error:raise SystemExit(7)
# Deliberately no reward: transport success is not a benchmark/model score.
Path(sys.argv[2]).write_text(json.dumps({'harbor_runtime':request['case']['runtime']['harbor_runtime'],'judge_identity':request['case']['runtime']['judge_identity'],'task_run':False}))
'''.replace('AUDIT',repr(str(audit))).replace('TIMEOUT','0.03' if mode=='timeout' else '2').replace('__CONTROL_MODE__',repr(mode)))
    original=verify._execute_harbor
    monkeypatch.setattr(verify,'_execute_harbor',lambda entry,*a,**k:original(entry,*a,**k,entrypoint=worker))
    output=subprocess.check_output
    def metadata(argv,*a,**k):
        if argv[:4]==['docker','network','inspect','bridge']:
            return json.dumps([{'IPAM':{'Config':[{'Gateway':'127.0.0.1'}]}}])
        if argv and argv[0]=='docker':pytest.fail('non-metadata Docker operation')
        return output(argv,*a,**k)
    monkeypatch.setattr(subprocess,'check_output',metadata)
    gateways=[];actual=judge_gateway.open_gateway
    def open_gateway(*a):
        value=actual(*a);gateways.append(value);return value
    monkeypatch.setattr(judge_gateway,'open_gateway',open_gateway)
    with fake_judge('timeout' if mode=='timeout' else 'reject' if mode=='reject' else 'success') as (base,requests):
        monkeypatch.setenv('HG_JUDGE_HARBOR_BASE_URL',base);monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','authored-judge')
        monkeypatch.setenv('HG_JUDGE_HARBOR_API_KEY','authored-judge-key')
        monkeypatch.setenv('HG_AGENT_API_KEY','actor-key-must-not-cross')
        # Pin this task-owned server selection before invoking the native wrapper.
        from harnessgrad.native_judge_identity import selection
        case['runtime'].update(selection())
        raw=json.dumps(case,sort_keys=True,separators=(',',':'));sha=hashlib.sha256(raw.encode()).hexdigest()
        (root/'case.json').write_text(raw)
        spec['native_benchmark']['case_sha256']=sha
        spec['native_judge_identity']=case['runtime']['judge_identity']
        sealed=json.loads((record/'episode.json').read_text())
        sealed['payload']['case_sha256']=sha
        (record/'episode.json').write_text(json.dumps(sealed))
        result=verify.run(spec,root,task_id=task_id,run_id=run_id,trial_id=trial_id,timeout_s=20)
    assert len(gateways)==1 and audit.is_file(),result
    facts=json.loads(audit.read_text())
    assert facts['actor_keys_present'] is False
    assert result['score'] is None and result['outcome']=='unmeasured'
    assert requests[0]['path']=='/authored/v1/chat/completions' and requests[0]['judge_auth_only'] is True
    if mode=='success':assert facts['http_status']==200 and facts['error_type'] is None
    elif mode=='reject':assert facts['http_status']==403
    elif mode=='timeout':assert facts['error_type'] in ('TimeoutError','URLError')
    else:assert facts['http_status']==200 and facts['error_type']=='RuntimeError'
    assert gateways[0]._server is None and gateways[0]._thread is None and closed(gateways[0].port)
    assert 'authored-judge-key' not in json.dumps(result) and 'actor-key-must-not-cross' not in json.dumps(result)
