"""Real Docker controls for the opt-in peer backend; no model experiment."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from eval.containers import observed
from eval.run import runner
from test_shared_dataset_runtime import harness, TRANSPORT
from test_shared_judge_trials_workers import endpoint


@pytest.fixture(scope='module')
def image(tmp_path_factory):
    # No pull, network or external data. An image-local repository proves that
    # same-path tmpfs initialization preserves image bytes instead of hiding them.
    try:
        base = json.loads(observed.checked('image', 'inspect', 'python:3.12.8-slim'))[0]['Id']
    except (OSError, RuntimeError):
        pytest.skip('local python:3.12.8-slim and Docker daemon required')
    folder = tmp_path_factory.mktemp('observed-image')
    (folder / 'Dockerfile').write_text(f'FROM {base}\nCOPY marker /workspace/marker\nCOPY reward /workspace/verifier/reward.txt\n')
    (folder / 'marker').write_text('from the pinned image')
    (folder / 'reward').write_text('1')
    name = 'hg-observed-test-' + str(os.getpid())
    build = observed.bounded(['env', 'DOCKER_BUILDKIT=0', 'docker', 'build', '--network=none',
                              '--pull=false', '-t', name, str(folder)], timeout=120)
    assert build.returncode == 0, build.stderr.decode()
    value = json.loads(observed.checked('image', 'inspect', name))[0]['Id']
    yield value
    observed.checked('image', 'rm', name)


def spec(image, **changes):
    return {'kind':'exec', 'backend':'observed-v1', 'image':image, 'workdir':'/workspace',
            'image_workspace':'/workspace', 'cpus':1, 'memory_mb':256, 'pids':64,
            'filesystems':{'/workspace':{'bytes':64 << 20,'inodes':4096}}, **changes}


def binding():
    return {'adapter_id':'fixture','run_id':'runtime-control','trial_id':'1','manifest_sha256':'a'*64}


def test_real_kernel_observation_and_quiescent_bounded_export(image):
    runtime = observed.Runtime(spec(image), binding=binding(), timeout_s=10)
    with runtime:
        assert runtime.receipt['admission']['status']=='passed'
        code = '''from pathlib import Path
import os,subprocess,sys
assert Path('marker').read_text()=='from the pinned image'
assert os.getuid()==65534
s=Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in s and 'NoNewPrivs:\\t1' in s
try: Path('/usr/actor-file').write_text('bad')
except OSError: pass
else: raise AssertionError('root is writable')
Path('patch.diff').write_text('output')
subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'],start_new_session=True,
                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
'''
        proc=runtime.run(['/usr/local/bin/python3','-c',code])
        assert proc.returncode==0,proc.stderr
        got=runtime.collect(['patch.diff','missing'])
        assert base64.b64decode(got['patch.diff']['base64'])==b'output'
        assert got['missing']['status']=='missing'
    assert runtime.receipt['cleanup']=={'complete':True,'errors':[]}
    actual=runtime.receipt['observed']
    assert actual['resources']==runtime.policy['resources']
    assert set(actual['filesystems'])=={'/workspace','/tmp','/dev','/dev/shm'}
    assert actual['attachments']==[]


def test_kernel_mismatch_refuses_before_candidate(image,monkeypatch):
    original=observed.observe
    def wrong(name,binding,policy,**kwargs):
        policy=deepcopy(policy);policy['resources']['memory_bytes']+=4096
        return original(name,binding,policy,**kwargs)
    monkeypatch.setattr(observed,'observe',wrong)
    runtime=observed.Runtime(spec(image),binding=binding(),timeout_s=3)
    with pytest.raises(RuntimeError,match='resource_limit_mismatch'):
        with runtime: pytest.fail('unobserved candidate launch')
    assert runtime.receipt['cleanup']['complete']


@pytest.mark.parametrize('payload',[
    "Path('patch.diff').symlink_to('/etc/passwd')",
    "os.mkfifo('patch.diff')",
    "Path('original').write_text('x');os.link('original','patch.diff')",
    "Path('patch.diff').write_bytes(b'x'*1025)",
])
def test_invalid_exports_cannot_be_submissions(image,payload):
    runtime=observed.Runtime(spec(image),binding=binding(),timeout_s=5)
    with runtime:
        p=runtime.run(['/usr/local/bin/python3','-c','from pathlib import Path;import os;'+payload])
        assert p.returncode==0,p.stderr
        with pytest.raises(RuntimeError):runtime.collect(['patch.diff'],max_bytes=1024)
    assert runtime.receipt['cleanup']['complete']


def test_timeout_kills_worker_and_removes_container(image):
    runtime=observed.Runtime(spec(image),binding=binding(),timeout_s=.3)
    with pytest.raises(subprocess.TimeoutExpired):
        with runtime:runtime.run(['/usr/local/bin/python3','-c','import time;time.sleep(600)'])
    assert runtime.receipt['cleanup']['complete']


def test_explicit_large_artifacts_and_scratch_keep_logs_as_retention_only(image):
    filesystems = {path: {'bytes': 96 << 20, 'inodes': 8192}
                   for path in ('/workspace', '/tmp', '/dev', '/dev/shm')}
    runtime = observed.Runtime(spec(image, filesystems=filesystems), binding=binding(), timeout_s=20)
    with runtime:
        code = '''from pathlib import Path
import sys
Path('/tmp/large-build-output').write_bytes(b'x' * (65 << 20))
Path('patch.diff').write_bytes(b'p' * ((16 << 20) + 1))
Path('trace.jsonl').write_bytes(b'l' * (9 << 20))
sys.stdout.write('o' * (9 << 20))
sys.stderr.write('e' * ((1 << 20) + 1))
Path('done').write_text('yes')
'''
        proc = runtime.run(['/usr/local/bin/python3', '-c', code])
        assert proc.returncode == 0
        assert runtime.receipt['console_logs']['stdout']['truncated']
        assert runtime.receipt['console_logs']['stderr']['truncated']
        artifacts = runtime.collect(['patch.diff', 'done'], max_bytes=None)
        assert artifacts['patch.diff']['bytes'] == (16 << 20) + 1
        assert base64.b64decode(artifacts['done']['base64']) == b'yes'
        with pytest.raises(RuntimeError, match='byte limit'):
            runtime.collect(['patch.diff'])  # Omission keeps the old 16 MiB policy.
        logs = runtime.collect(['trace.jsonl'], max_bytes=8 << 20, truncate=True)
        assert logs['trace.jsonl']['source_bytes'] == 9 << 20
        assert logs['trace.jsonl']['bytes'] == 8 << 20 and logs['trace.jsonl']['truncated']
        assert runtime.receipt['observed']['filesystems']['/tmp']['bytes'] == 96 << 20
    assert runtime.receipt['cleanup']['complete']


@pytest.mark.parametrize('use_model', [False, True])
def test_pair_uses_candidate_in_two_images_and_fresh_observed_verifier(image,tmp_path,monkeypatch,endpoint,use_model):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai' if use_model else 'mock')
    monkeypatch.setenv('HG_AGENT_BASE_URL', os.environ['HG_JUDGE_BASE_URL'])
    monkeypatch.setenv('HG_AGENT_MODEL', 'fixture-model')
    monkeypatch.setenv('HG_JUDGE_API_KEY','judge-secret')
    monkeypatch.setenv('HG_ENV_API_KEY','environment-secret')
    repo=harness(tmp_path,'''import time,json
from transport import call
if os.environ['HG_AGENT_BACKEND'] != 'mock':
 import urllib.request
 req=urllib.request.Request(os.environ['HG_AGENT_BASE_URL']+'/chat/completions',
   data=json.dumps({'model':'fixture-model','messages':[{'role':'user','content':'ok'}]}).encode(),
   headers={'Content-Type':'application/json'})
 response=json.load(urllib.request.urlopen(req,timeout=10))
 assert response['choices'][0]['message']['content']=='safe fixture score'
assert Path('marker').read_text()=='from the pinned image'
assert 'HG_ENV_API_KEY' not in os.environ and 'HG_JUDGE_API_KEY' not in os.environ
assert not Path('worker_record').exists()
me=os.environ['HG_WORKER_ID'];peer='b' if me=='a' else 'a'
call('/run/harnessgrad-tool/tool.sock','send',{'to':peer,'message':'hello '+me})
for _ in range(300):
 messages=call('/run/harnessgrad-tool/tool.sock','receive',{})
 if messages: break
 time.sleep(.02)
assert messages[0]['from']==peer
(w/'patch.diff').write_text(me+':'+messages[0]['message'])
(w/'trace.jsonl').write_text(json.dumps({'usage':{'input_tokens':2,'output_tokens':3,'calls':1}})+'\\n')
''')
    workers=[{'id':name,'goal':'fixture','env':spec(image),'artifacts':['patch.diff'], 'artifact_max_bytes': None,
              'model_route': dict.fromkeys(('max_connections', 'request_max_bytes', 'response_max_bytes', 'header_timeout_s')),
              'files':[{'path':'transport.py','from':str(TRANSPORT)}],'timeout_s':15} for name in ('a','b')]
    check='''from pathlib import Path
import json,os
assert os.getuid()==65534
assert Path('marker').read_text()=='from the pinned image'
assert Path('workers/a/patch.diff').read_text()=='a:hello b'
assert Path('workers/b/patch.diff').read_text()=='b:hello a'
r=json.loads(Path('worker_record/workers.json').read_text())
assert len(r['events'])==2
try: Path('worker_record/workers.json').write_text('forged')
except OSError: pass
else: raise AssertionError('record is writable')
Path('verifier/reward.txt').write_text('1')
'''
    verifier={'kind':'command','argv':['python3','check.py'],'inputs':[{'path':'check.py','content':check}],
              'reward_file':'/verifier/reward.txt','container_runtime':spec(image),
              'result_contract':{'version':1,'score_range':[0,1]}}
    result=runner.run_one(repo,{'task_id':'pair'},setup={'workers':workers},verifier=verifier,
                          run_id='docker-pair',trial_id='trial-1',recordings_root=tmp_path/'records')
    assert result.get('verdict',{}).get('score')==1,result
    assert result['verdict']['container_runtime']['admission']['status']=='passed'
    assert result['verdict']['container_runtime']['cleanup']['complete']
    for row in result['worker_group']['workers'].values():
        assert row['runtime_services']['container']['admission']['status']=='passed'
        assert row['runtime_services']['container']['cleanup']['complete']
    assert result['runtime_services']['workers']['cleanup']['complete']
    assert len(endpoint) == (2 if use_model else 0)


def test_legacy_exec_stays_refused_and_unpinned_worker_is_rejected(tmp_path):
    repo=harness(tmp_path,'pass')
    result=runner.run_one(repo,{'task_id':'old'},env={'kind':'exec','image':'python:3.12.8-slim'})
    assert result['invalid']['receipt']['reason']=='backend_observation_unavailable'
    with pytest.raises(ValueError,match='digest'): observed.validate(spec('python:3.12.8-slim'))


def test_worker_model_route_rejects_other_operations(endpoint):
    import http.client
    import socket
    from eval.service.worker_model_route import ModelRoute
    route=ModelRoute(os.environ['HG_JUDGE_BASE_URL'],timeout_s=2).start()
    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self.sock.connect(str(route.path))
    try:
        for method,path in [('GET','/v1/models'),('POST','/v1/embeddings'),('CONNECT','somewhere:443')]:
            conn=UnixConnection('localhost')
            conn.request(method,path,body='{}');reply=conn.getresponse()
            assert reply.status==403
            reply.read();conn.close()
        assert not endpoint
        assert len(route.faults)==3
    finally:route.stop()


def test_worker_failure_keeps_peer_receipts_and_suppresses_score(image,tmp_path,monkeypatch):
    monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    repo=harness(tmp_path,"""import time
if os.environ['HG_WORKER_ID']=='a':time.sleep(600)
(w/'patch.diff').write_text('valid peer')
""")
    workers=[{'id':name,'goal':'fixture','env':spec(image),'artifacts':['patch.diff'],
              'timeout_s':.3 if name=='a' else 10} for name in ('a','b')]
    result=runner.run_one(repo,{'task_id':'timeout-pair'},setup={'workers':workers},
        verifier={'kind':'command','argv':['true']},recordings_root=tmp_path/'archive')
    assert result['invalid']['stage']=='worker execution'
    rows=result['worker_group']['workers']
    assert rows['a']['invalid']['stage']=='worker timeout'
    assert rows['b']['artifacts']['patch.diff']['status']=='submitted'
    assert (Path(result['worker_group']['archive'])/'workers/b/patch.diff').read_text()=='valid peer'
    for row in rows.values():assert row['runtime_services']['container']['cleanup']['complete']
    assert result['runtime_services']['workers']['cleanup']['complete']


def test_image_cannot_supply_a_stale_verifier_reward(image,tmp_path):
    from eval.verification.verifier import _verify
    verdict=_verify(tmp_path,{'kind':'command','argv':['python3','-c','pass'],
        'reward_file':'/verifier/reward.txt','container_runtime':spec(image),
        'result_contract':{'version':1,'score_range':[0,1]}},'',sandbox=True,timeout_s=5)
    assert verdict['score'] is None and verdict['outcome']=='unmeasured',verdict
    assert 'wrote no' in verdict['unmeasured']
    assert verdict['container_runtime']['cleanup']['complete']


def test_verifier_result_export_can_explicitly_exceed_legacy_ceiling(image, tmp_path):
    from eval.verification.verifier import _verify
    code = "from pathlib import Path;import json;Path('verifier/reward.txt').write_text('1');Path('verifier/evidence.json').write_text(json.dumps({'detail':'x'*(5<<20)}))"
    verifier = {'kind': 'command', 'argv': ['python3', '-c', code],
        'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
        'container_runtime': spec(image), 'result_contract': {'version': 1, 'score_range': [0, 1]}}
    result = _verify(tmp_path, {**verifier, 'result_max_bytes': None}, '', sandbox=True, timeout_s=10)
    assert result['score'] == 1, result.get('unmeasured')
    assert len(result['evidence']['detail']) == 5 << 20
    assert result['container_runtime']['cleanup']['complete']
    result = _verify(tmp_path, verifier, '', sandbox=True, timeout_s=10)
    assert result['score'] is None
    assert 'byte limit' in result['unmeasured']
    assert result['container_runtime']['cleanup']['complete']
