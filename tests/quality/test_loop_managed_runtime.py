"""Shipped Loop: real SDK, native/container isolation, no model or released task."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import uuid
from types import SimpleNamespace
import venv

import pytest

from eval.containers import container
from eval.run import runner
from eval.service import container_observation
from harnessgrad import candidate_runtime, config, python_exec_runtime as runtime
from harnessgrad import python_harness_runtime as native

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT/'base_harness/loop'
SHELL_CONTROL = (
    "if command -v python >/dev/null; then "
    "python -I -B -c 'import sys; print(sys.executable)'; "
    "else printf PYTHON_ABSENT; fi > child-python.txt; "
    "printf LOOP_CONTROL > tool-control.txt"
)


@pytest.fixture(scope='module')
def loop_store():
    if not shutil.which('uv') or not shutil.which('bwrap'):
        pytest.skip('Loop controls require uv, bubblewrap and cached locked wheels')
    root = Path(tempfile.mkdtemp(prefix='hg-loop-test-', dir=Path.home()/'.cache'))
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
            patch.setenv('UV_OFFLINE', '1')
            patch.setenv('UV_PYTHON_DOWNLOADS', 'never')
            config.reset()
            runtime.prepare(HARNESS, build=True, root=root/'store')
        yield root
    finally:
        config.reset()
        native.remove_instance(root)


@pytest.fixture
def managed_loop(loop_store, tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('HG_AGENT_', 'HG_METHOD_', 'HG_HARNESS_')):
            monkeypatch.delenv(key)
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT', str(loop_store/'store'))
    monkeypatch.setenv('UV_OFFLINE', '1')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_AGENT_MAX_STEPS', '3')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    config.reset()
    try:
        yield SimpleNamespace(root=loop_store)
    finally:
        config.reset()


@pytest.fixture(scope='module')
def loop_images(loop_store):
    if container.available() is None:
        pytest.skip('real container controls require a local Docker daemon')
    try:
        images = {name: container.resolve_digest(tag) for name, tag in
                  [('python', 'python:3.12.8-slim'), ('absent', 'ubuntu:24.04')]}
    except container.ContainerUnavailable as exc:
        pytest.skip('import the authored-control base images first: '+str(exc))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('HG_HARNESS_RUNTIME_ROOT', str(loop_store/'store'))
        patch.setenv('UV_OFFLINE', '1')
        patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
        config.reset()
        runtime.prepare(HARNESS, envs={name: {'kind':'exec', 'image':image}
                                      for name,image in images.items()}, build=True)
    return images


@contextmanager
def endpoint():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            action = ({'tool':'bash', 'command':SHELL_CONTROL}
                if len(body['messages']) == 2 else {'answer':'LOOP_CONTROL'})
            data = json.dumps({'id':'authored', 'object':'chat.completion', 'created':0,
                'model':body['model'], 'choices':[{'index':0, 'finish_reason':'stop',
                    'message':{'role':'assistant','content':json.dumps(action)}}],
                'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1',seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


PROBE = '''
import importlib.util as _u,json as _j,os as _o,sys as _s
from pathlib import Path as _P
import openai as _sdk
_work=_P(_s.argv[_s.argv.index('--workdir')+1])
_home=_P(_o.environ['HOME'])/'counter'
_state=_P(_o.environ['HARNESSGRAD_STATE'])/'counter'
_previous=[p.exists() for p in (_home,_state)]
for _p in (_home,_state): _p.write_text('task-private')
try:
 _P(_sdk.__file__).write_text('changed')
 _denied=False
except OSError:
 _denied=True
(_work/'runtime-probe.json').write_text(_j.dumps({
 'python':_s.executable,'prefix':_s.prefix,'package':_sdk.__file__,
 'isolated':bool(_s.flags.isolated),'readonly':bool(_o.statvfs(_s.prefix).f_flag & _o.ST_RDONLY),
 'write_denied':_denied,'previous':_previous,'home':str(_home.parent),
 'pytest_visible':_u.find_spec('pytest') is not None,
 'task_dependency_visible':_u.find_spec('hg_loop_image_dependency') is not None,
 'task_path_dependency_visible':_u.find_spec('hg_loop_path_dependency') is not None,
 'platform_visible':_P(PLATFORM_PREFIX).exists(),
 'private_visible':(_work/'verifier-private.txt').exists(),
 'entrypoint':_o.environ.get('HG_AUTHORED_IMAGE_ENTRYPOINT'),
 'startup_ready':(_work/'entrypoint-ready').is_file(),
 'python_env':{k:v for k,v in _o.environ.items() if k in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE')}}))
'''.replace('PLATFORM_PREFIX',repr(sys.prefix))


def source_with_probe(tmp_path, *, sdk_mock=False, command=SHELL_CONTROL):
    source=tmp_path/'source'
    shutil.copytree(HARNESS,source)
    entry=source/'agent.py'
    entry.write_text(entry.read_text().replace('from __future__ import annotations\n',
                    'from __future__ import annotations\n'+PROBE,1))
    entry.write_text(entry.read_text().replace('    run(task, Path(args.workdir))',
        '''    run(task, Path(args.workdir))
    _row=_j.loads((_work/'runtime-probe.json').read_text())
    _row['child_python']=(_work/'child-python.txt').read_text().strip()
    _row['task_environment']=_j.loads((_work/'task-environment.json').read_text()) if (_work/'task-environment.json').exists() else None
    _row['sdk_calls']=len(_j.loads((_work/'sdk-requests.json').read_text())) if (_work/'sdk-requests.json').exists() else None
    with (_work/'trace.jsonl').open('a') as _trace:
        _trace.write(_j.dumps({'runtime_control':_row})+'\\n')'''))
    if sdk_mock:
        replacement='''
import httpx as _httpx
_OpenAI=_sdk.OpenAI
def _response(request):
    body=_j.loads(request.content)
    path=_work/'sdk-requests.json'
    rows=_j.loads(path.read_text()) if path.exists() else []
    rows.append(body)
    path.write_text(_j.dumps(rows))
    action=({'tool':'bash','command':AUTHORED_COMMAND}
            if len(body['messages'])==2 else {'answer':'LOOP_CONTROL'})
    return _httpx.Response(200,json={'id':'authored','object':'chat.completion','created':0,
        'model':body['model'],'choices':[{'index':0,'finish_reason':'stop',
        'message':{'role':'assistant','content':_j.dumps(action)}}],
        'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}})
def _client(**kwargs):
    return _OpenAI(**kwargs,http_client=_httpx.Client(transport=_httpx.MockTransport(_response)))
_sdk.OpenAI=_client
_call_mock=_call_openai
'''.replace('AUTHORED_COMMAND',repr(command))
        entry.write_text(entry.read_text().replace('if __name__ == "__main__":',
                         replacement+'\nif __name__ == "__main__":'))
    return source


def test_empty_platform_can_prepare_and_check_loop_without_pip_or_sdk(managed_loop,tmp_path):
    platform=tmp_path/'platform'
    venv.EnvBuilder(with_pip=False).create(platform)
    code='''import importlib.util,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
for name in ('pip','openai','httpx','pyarrow'):
 assert importlib.util.find_spec(name) is None,name
from harnessgrad import candidate_runtime as r
value=r.prepare(Path(sys.argv[2]))
assert value['cache_hit'] and not value['installed']
assert r.admit(Path(sys.argv[2]),value['identity'])==value['identity']
for name in ('pip','openai','httpx','pyarrow'):
 assert importlib.util.find_spec(name) is None,name
print(json.dumps(value))
'''
    proc=subprocess.run([str(platform/'bin/python'),'-I','-B','-c',code,str(ROOT),str(HARNESS)],
        env=dict(os.environ),cwd=tmp_path,capture_output=True,text=True,timeout=120)
    (tmp_path/'empty-platform.log').write_text(proc.stdout+proc.stderr)
    assert proc.returncode==0,proc.stdout+proc.stderr


@pytest.mark.parametrize('placement',['files','image-mount','fallback-mount','image-container','fallback-container'])
def test_real_loop_sdk_shell_and_private_scorer(managed_loop,loop_images,tmp_path,monkeypatch,placement):
    source=source_with_probe(tmp_path,sdk_mock=placement!='files')
    if placement=='files':
        env={'kind':'files','network':'none'}
    else:
        image=loop_images['absent' if placement.startswith('fallback') else 'python']
        env={'kind':'exec','image':image,'workdir':'/work','network':'none'}
        if placement.endswith('container'):
            env['state']='container'
    binding=candidate_runtime.prepare(source,envs={'t':env})['identity']
    selected=runtime.placement(binding,env)
    artifact=selected['artifact']
    if placement!='files':
        assert (selected['base'] is not None)==placement.startswith('image'),selected
    if env['kind']=='exec':
        observation=container_observation.observe_for(env['image'],env,network='none',
            run_id='authored-loop-control',read_only=env.get('state')!='container')
        assert observation.ok,observation.receipt()
        observation=observation.receipt()
    else:
        observation=None
    verifier_code='''import importlib.util,json
from pathlib import Path
row=json.loads(Path('runtime-probe.json').read_text())
assert Path('verifier-private.txt').read_text()=='private-control'
assert Path('answer.txt').read_text().strip()=='LOOP_CONTROL'
assert Path('tool-control.txt').read_text()=='LOOP_CONTROL'
assert row['isolated'] and row['readonly'] and row['write_denied']
assert row['previous']==[False,False] and row['python_env']=={}
assert not any(row[k] for k in ('pytest_visible','platform_visible','private_visible'))
assert importlib.util.find_spec('openai') is None
assert not Path(ACTOR_PYTHON).exists()
print('SDK, actual shell, readonly artifact and private scorer verified')
'''.replace('ACTOR_PYTHON',repr(artifact['python']))
    scorer_python=selected['base']['executable'] if selected['base'] else sys.executable
    verifier={'kind':'command','argv':[scorer_python,'-I','-B','check.py'],
              'inputs':[{'path':'check.py','content':verifier_code},
                        {'path':'verifier-private.txt','content':'private-control'}]}
    if placement.startswith('fallback'):
        verifier={'kind':'command','argv':['sh','-c',
            'test "$(cat verifier-private.txt)" = private-control && '
            'test "$(cat answer.txt)" = LOOP_CONTROL && '
            'test "$(cat tool-control.txt)" = LOOP_CONTROL && '
            '! test -x '+shlex.quote(artifact['python'])],
            'inputs':[{'path':'verifier-private.txt','content':'private-control'}]}
    with endpoint() as (base,seen):
        monkeypatch.setenv('HG_AGENT_BACKEND','openai' if placement=='files' else 'mock')
        monkeypatch.setenv('HG_AGENT_BASE_URL',base)
        monkeypatch.setenv('HG_AGENT_API_KEY','authored-loop-key')
        monkeypatch.setenv('HG_AGENT_MODEL','authored-loop-model')
        result=runner.run_one(source,{'task_id':'t','goal':'Write a short final answer.'},env=env,
            verifier=verifier,timeout_s=60,verify_timeout_s=30,run_id='authored-loop-control',
            observation=observation,harness_runtime_identity=binding)
    (tmp_path/'result.json').write_text(json.dumps(result,indent=2))
    assert not result.get('invalid'),result
    assert result['exit_code']==0,result.get('stderr')
    assert result['answer'].strip()=='LOOP_CONTROL'
    assert result['verdict']['passed'],result['verdict']
    assert len(seen)==(2 if placement=='files' else 0)
    assert result['harness_runtime_identity']==binding
    assert result['harness_runtime']['interpreter']==artifact['python']
    assert result['harness_runtime']['placement']==selected['placement']
    row=next(json.loads(line)['runtime_control'] for line in result['trace'].splitlines()
             if 'runtime_control' in json.loads(line))
    assert row['python']==artifact['python']
    if placement=='files':
        assert row['child_python']==artifact['python']
    elif selected['base']:
        assert row['child_python']!=artifact['python']
        assert Path(row['child_python']).parent==Path(selected['base']['executable']).parent
    else:
        # The fallback supplies the actor, not a new task Python installation.
        assert row['child_python']=='PYTHON_ABSENT'
    assert row['prefix']==artifact['path'] and row['isolated']
    assert row['readonly'] and row['write_denied'] and row['previous']==[False,False]
    assert row['python_env']=={} and not any(row[key] for key in
           ('pytest_visible','platform_visible','private_visible'))
    if placement!='files':
        assert row['sdk_calls']==2
    if selected['base']:
        assert artifact['runtime_paths']==[artifact['path']]
        assert selected['base']['executable'].startswith('/usr/local/')


def test_shipped_loop_h0_and_snapshot_resume(managed_loop,tmp_path,request):
    ids=tmp_path/'ids.txt'
    ids.write_text('t01\n')
    runs,work=tmp_path/'runs',tmp_path/'work'
    env=dict(os.environ,HG_AGENT_MODEL='loop-mock',HG_AGENT_BASE_URL='')
    command=[sys.executable,'-B',str(ROOT/'tools/prepare_h0.py'),'--dataset','demo',
             '--harness','loop','--study-ids',str(ids),'--model','loop-mock','--endpoint','',
             '--jobs','1','--runs-root',str(runs)]
    def call(argv,label):
        p=subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,text=True,timeout=150)
        (tmp_path/(label+'.log')).write_text(p.stdout+p.stderr)
        assert p.returncode==0,p.stdout+p.stderr
        return p
    call(command,'h0-create')
    donor=next(path for path in runs.glob('h0-demo-*') if path.is_dir())
    request.addfinalizer(lambda: shutil.rmtree(Path('/tmp/hg-h0-work')/donor.name,ignore_errors=True))
    assert 'reusing' in call(command,'h0-reuse').stdout
    common=[sys.executable,'-B',str(ROOT/'driver.py'),'--harness','loop','--dataset','demo',
            '--sampling','all','--jobs','1','--trials','1','--no-summary',
            '--runs-root',str(runs),'--work-root',str(work)]
    train=[*common,'--tasks','t01','--run-id','study','--rounds','1',
           '--method-entrypoint',f'{sys.executable} {ROOT/"methods/noop/run.py"}',
           '--h0-from',donor.name,'--reuse-identical']
    call(train,'train')
    study=runs/'study'
    points=[json.loads(line) for line in (study/'curve.jsonl').read_text().splitlines()]
    binding=points[0]['harness_runtime_identity']
    assert binding['kind']==runtime.KIND
    assert points[0]['shared_from']['run_id']==donor.name
    assert all(p['harness_runtime_identity']==binding and p['n_invalid']==0 for p in points)
    for n in (0,1):
        shutil.rmtree(study/f'state_after_round{n}')
    before=(study/'curve.jsonl').read_bytes()
    call([*train,'--rounds','2','--resume'],'resume')
    assert (study/'curve.jsonl').read_bytes().startswith(before)
    resumed=[json.loads(line) for line in (study/'curve.jsonl').read_text().splitlines()]
    assert resumed[-1]['round']==2 and resumed[-1]['harness_runtime_identity']==binding
    assert resumed[-1]['n_invalid']==0


@pytest.mark.parametrize('kind',['files','exec'])
def test_tasks_reuse_readonly_dependencies_and_start_with_private_state(
        managed_loop,loop_images,tmp_path,monkeypatch,kind):
    source=source_with_probe(tmp_path,sdk_mock=True)
    env=({'kind':'files','network':'none'} if kind=='files' else
         {'kind':'exec','image':loop_images['python'],'workdir':'/work','network':'none'})
    binding=candidate_runtime.prepare(source,envs={'t':env})['identity']
    monkeypatch.setenv('HG_AGENT_BASE_URL','http://authored.invalid/v1')
    monkeypatch.setenv('HG_AGENT_MODEL','authored-loop-model')
    monkeypatch.setenv('HG_AGENT_API_KEY','authored-key')
    observation=(container_observation.observe_for(env['image'],env,network='none').receipt()
                 if kind=='exec' else None)
    rows=[]
    for index in range(2):
        result=runner.run_one(source,{'task_id':f'private-{index}','goal':'Write an answer.'},env=env,
            verifier={'kind':'answer','expected':'LOOP_CONTROL'},observation=observation,
            harness_runtime_identity=binding,timeout_s=60,run_id='authored-loop-state')
        assert not result.get('invalid') and result['exit_code']==0,result
        assert result['verdict']['passed'],result['verdict']
        assert result['harness_runtime_identity']==binding
        rows.append(next(json.loads(line)['runtime_control'] for line in result['trace'].splitlines()
                         if 'runtime_control' in json.loads(line)))
    assert all(row['previous']==[False,False] and row['readonly'] for row in rows)
    assert len({row['prefix'] for row in rows})==1
    if kind=='files':
        assert len({row['home'] for row in rows})==2


def test_container_candidate_cache_checkpoint_eval_and_resume(
        managed_loop,loop_images,tmp_path,monkeypatch):
    import driver
    from data.scaffolds import registry
    source=source_with_probe(tmp_path,sdk_mock=True)
    original_harness=driver._harness_by_name
    monkeypatch.setattr(driver,'_harness_by_name',
        lambda name:source if name=='loop' else original_harness(name))
    original_envs=registry.load_envs
    image=loop_images['python']
    def envs(name,*,task_ids=None):
        if name=='demo':
            ids=task_ids if task_ids is not None else [f't{i:02}' for i in range(1,9)]
            return {tid:{'kind':'exec','image':loop_images['absent'] if int(tid[1:])>=5 else image,
                         'workdir':'/work','network':'none'} for tid in ids}
        return original_envs(name,task_ids=task_ids)
    monkeypatch.setattr(registry,'load_envs',envs)
    def observations(envs,tasks,*,run_id=''):
        return {task['task_id']:container_observation.observe_for(
            envs[task['task_id']]['image'],envs[task['task_id']],network='none',
            run_id=run_id,read_only=True).receipt() for task in tasks}
    # The production policy deliberately auto-probes only service environments.
    # This authored plain-container control supplies real kernel observations.
    monkeypatch.setattr(runner,'_observations_for',observations)
    monkeypatch.setenv('HG_AGENT_BASE_URL','http://authored.invalid/v1')
    monkeypatch.setenv('HG_AGENT_MODEL','authored-loop-model')
    monkeypatch.setenv('HG_AGENT_API_KEY','authored-key')
    runs,work=tmp_path/'runs',tmp_path/'work'
    common=['--harness','loop','--dataset','demo','--sampling','all','--jobs','1',
            '--trials','1','--no-summary','--runs-root',str(runs),'--work-root',str(work)]
    train=[*common,'--tasks','t01','--run-id','image-study','--rounds','1',
           '--method-entrypoint',f'{sys.executable} {ROOT/"methods/noop/run.py"}', '--reuse-identical']
    def call(argv):
        monkeypatch.setattr(sys,'argv',['driver.py',*argv])
        return driver.main()
    assert call(train)==0
    run=runs/'image-study'
    points=[json.loads(line) for line in (run/'curve.jsonl').read_text().splitlines()]
    binding=points[0]['harness_runtime_identity']
    assert image in binding['images']
    assert all(point['n_invalid']==0 and point['harness_runtime_identity']==binding for point in points)
    assert points[1].get('shared_from') or points[1].get('identical_to') or points[1].get('cached_tasks')
    for n in (0,1):
        shutil.rmtree(run/f'state_after_round{n}')
    before=(run/'curve.jsonl').read_bytes()
    assert call([*train,'--rounds','2','--resume'])==0
    assert (run/'curve.jsonl').read_bytes().startswith(before)
    resumed=[json.loads(line) for line in (run/'curve.jsonl').read_text().splitlines()]
    assert resumed[-1]['round']==2 and resumed[-1]['harness_runtime_identity']==binding
    assert resumed[-1]['n_invalid']==0
    republished=runtime.prepare(source,envs={'t':{'kind':'exec','image':image}},
                                build=True,force=True)['identity']
    assert republished['native']['artifact']!=binding['native']['artifact']
    exam=[*common,'--side','eval','--from-run','image-study','--from-round','2',
          '--tasks','t05','--run-id','image-exam','--rounds','0']
    assert call(exam)==0
    measured=[json.loads(line) for line in (runs/'image-exam/curve.jsonl').read_text().splitlines()]
    exam_binding=measured[-1]['harness_runtime_identity']
    assert exam_binding['native']==binding['native']
    assert all(exam_binding['images'][key]==value for key,value in binding['images'].items())
    assert loop_images['absent'] in exam_binding['images']
    assert measured[-1]['evaluated_from']['harness_runtime_identity']==binding
    assert measured[-1]['n_invalid']==0
    prior={path.relative_to(runs/'image-exam'):path.read_bytes()
           for path in (runs/'image-exam/progress').rglob('*') if path.is_file()}
    def no_actor(*args,**kwargs):
        pytest.fail('completed eval resume launched an actor')
    monkeypatch.setattr(runner,'run_one',no_actor)
    assert call([*exam,'--resume'])==0
    current=[json.loads(line) for line in (runs/'image-exam/curve.jsonl').read_text().splitlines()]
    assert current[-1]['harness_runtime_identity']==exam_binding
    assert current[-1]['evaluated_from']==measured[-1]['evaluated_from']
    assert current[-1]['per_task']==measured[-1]['per_task'] and current[-1]['n_invalid']==0
    assert all((runs/'image-exam'/path).read_bytes()==data for path,data in prior.items())


@pytest.fixture(scope='module',params=['system','venv'])
def task_python_image(loop_images,tmp_path_factory,request):
    """An image's task tools/dependencies, independent of the actor's SDK venv."""
    root=tmp_path_factory.mktemp('task-image-'+request.param)
    module=root/'hg_loop_image_dependency.py'
    module.write_text("VALUE='IMAGE_DEPENDENCY_OK'\n")
    extras=root/'path-dependencies'
    extras.mkdir()
    (extras/'hg_loop_path_dependency.py').write_text("VALUE='IMAGE_PATH_OK'\n")
    tools=root/'task-tools'
    tools.mkdir()
    tool=tools/'hg-loop-task-tool'
    tool.write_text('#!/bin/sh\nprintf TASK_TOOL_OK\n')
    tool.chmod(0o755)
    name='hg-loop-task-env-'+uuid.uuid4().hex
    tag=name+':control'
    created=False
    image=None
    prefix='/opt/hg-loop-task-venv' if request.param=='venv' else '/usr/local'
    task_path='/opt/hg-loop-task-tools:'+prefix+'/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin'
    try:
        container.create(name,image=loop_images['python'],run_id=name,task_id='authored')
        created=True
        if request.param=='venv':
            container.start(name)
            result=container.exec_in(name,['/usr/local/bin/python3','-I','-B','-m','venv',
                                           '--without-pip',prefix],timeout_s=60)
            assert result.returncode==0,result.stderr
            container.stop(name)
        container.cp_in(name,module,prefix+'/lib/python3.12/site-packages/'+module.name)
        container.cp_in(name,extras,'/opt/hg-loop-task-path')
        container.cp_in(name,tools,'/opt/hg-loop-task-tools')
        changes=['--change','ENV PATH='+task_path,
                 '--change','ENV PYTHONPATH=/opt/hg-loop-task-path']
        if request.param=='venv':
            changes+=['--change','ENV VIRTUAL_ENV='+prefix]
        result=container.docker('commit',*changes,name,tag,timeout=120)
        assert result.returncode==0,result.stderr
        image=result.stdout.strip()
        # Positive control uses exactly the task image, without any actor runtime.
        work=root/'positive'
        work.mkdir()
        result=container.run(['sh','-c','hg-loop-task-tool && python -B -c '
            +shlex.quote('import hg_loop_image_dependency,hg_loop_path_dependency; '
                         'print(hg_loop_image_dependency.VALUE,hg_loop_path_dependency.VALUE)')],
            image=image,task_dir=work,task_mount='/work',cwd='/work',
            env=container.container_env('/work'),network='none',timeout_s=30)
        (root/'positive.json').write_text(json.dumps({'exit':result.returncode,
            'stdout':result.stdout,'stderr':result.stderr}))
        assert result.returncode==0 and 'IMAGE_DEPENDENCY_OK IMAGE_PATH_OK' in result.stdout
        yield {'image':image,'prefix':prefix,'path':task_path,'venv':request.param=='venv'}
    finally:
        if created:
            container.remove_container(name)
        if image is not None:
            container.remove_image(tag)


@pytest.mark.parametrize('state',['mount','container'])
def test_task_shell_keeps_image_python_dependencies_and_tools(
        managed_loop,task_python_image,tmp_path,monkeypatch,state):
    probe='''import importlib.util,json,os,sys
import hg_loop_image_dependency as installed
import hg_loop_path_dependency as extra
print(json.dumps({'python':sys.executable,'prefix':sys.prefix,'path':os.environ['PATH'],
 'virtual_env':os.environ.get('VIRTUAL_ENV'),'pythonpath':os.environ['PYTHONPATH'],
 'dependency':installed.VALUE,'extra':extra.VALUE,'actor_sdk_visible':importlib.util.find_spec('openai') is not None}))'''
    command=(SHELL_CONTROL+'; hg-loop-task-tool > task-tool.txt && '
             'python -B -c '+shlex.quote(probe)+' > task-environment.json && '
             'python3 -B -c '+shlex.quote('import hg_loop_image_dependency,hg_loop_path_dependency') )
    source=source_with_probe(tmp_path,sdk_mock=True,command=command)
    env={'kind':'exec','image':task_python_image['image'],'workdir':'/work','network':'none'}
    if state=='container':
        env['state']='container'
    binding=candidate_runtime.prepare(source,envs={'t':env})['identity']
    monkeypatch.setenv('HG_AGENT_BASE_URL','http://authored.invalid/v1')
    monkeypatch.setenv('HG_AGENT_MODEL','authored-loop-model')
    monkeypatch.setenv('HG_AGENT_API_KEY','authored-key')
    # Host paths/configuration must not become the task's environment.
    monkeypatch.setenv('PATH','/host-only-python-and-tools:'+os.environ['PATH'])
    monkeypatch.setenv('PYTHONPATH','/host-only-pythonpath')
    monkeypatch.setenv('VIRTUAL_ENV','/host-only-venv')
    observation=container_observation.observe_for(env['image'],env,network='none',
                                                 read_only=state!='container').receipt()
    verifier={'kind':'command','argv':['sh','-c',
        'test "$(cat task-tool.txt)" = TASK_TOOL_OK && python -B -c '
        +shlex.quote("import json,importlib.util; from pathlib import Path; "
                    "r=json.loads(Path('task-environment.json').read_text()); "
                    "assert r['dependency']=='IMAGE_DEPENDENCY_OK' and r['extra']=='IMAGE_PATH_OK'; "
                    "assert importlib.util.find_spec('openai') is None")]}
    result=runner.run_one(source,{'task_id':'task-environment','goal':'Write an answer.'},
        env=env,verifier=verifier,observation=observation,harness_runtime_identity=binding,
        timeout_s=60,run_id='authored-task-env-'+state)
    (tmp_path/'result.json').write_text(json.dumps(result,indent=2))
    assert not result.get('invalid') and result['exit_code']==0,result
    assert result['verdict']['passed'],result['verdict']
    row=next(json.loads(line)['runtime_control'] for line in result['trace'].splitlines()
             if 'runtime_control' in json.loads(line))
    artifact=runtime.placement(binding,env)['artifact']
    assert row['python']==artifact['python'] and row['prefix']==artifact['path']
    assert row['isolated'] and row['readonly'] and row['write_denied'] and row['sdk_calls']==2
    assert not row['task_dependency_visible'] and not row['task_path_dependency_visible']
    assert not row['platform_visible'] and not row['pytest_visible']
    task=row['task_environment']
    assert task['python']==row['child_python']!=artifact['python']
    assert task['prefix']==task_python_image['prefix']
    assert task['path']==task_python_image['path'] and task['pythonpath']=='/opt/hg-loop-task-path'
    assert task['virtual_env']==(task_python_image['prefix'] if task_python_image['venv'] else None)
    assert not task['actor_sdk_visible']
    commands=[json.loads(line) for line in result['trace'].splitlines() if 'command' in json.loads(line)]
    assert commands and all(row['exit']==0 for row in commands)


@pytest.mark.parametrize('state',['mount','container'])
def test_managed_actor_preserves_the_image_entrypoint(managed_loop,loop_images,tmp_path,monkeypatch,state):
    name='hg-loop-entrypoint-'+uuid.uuid4().hex
    tag=name+':control'
    entrypoint=['sh','-c','test -f /work/task.json && printf ready > /work/entrypoint-ready; '
                'export HG_AUTHORED_IMAGE_ENTRYPOINT=present; exec "$@"','authored']
    created=False
    image=None
    try:
        container.create(name,image=loop_images['python'],run_id=name,task_id='authored')
        created=True
        result=container.docker('commit','--change','ENTRYPOINT '+json.dumps(entrypoint),name,tag,timeout=120)
        assert result.returncode==0,result.stderr
        image=result.stdout.strip()
        env={'kind':'exec','image':image,'workdir':'/work','network':'none'}
        if state=='container':
            env['state']='container'
        source=source_with_probe(tmp_path,sdk_mock=True)
        binding=candidate_runtime.prepare(source,envs={'t':env})['identity']
        monkeypatch.setenv('HG_AGENT_BASE_URL','http://authored.invalid/v1')
        monkeypatch.setenv('HG_AGENT_MODEL','authored-loop-model')
        monkeypatch.setenv('HG_AGENT_API_KEY','authored-key')
        observation=container_observation.observe_for(image,env,network='none',read_only=state!='container').receipt()
        result=runner.run_one(source,{'task_id':'entrypoint','goal':'Write an answer.'},env=env,
            verifier={'kind':'command','argv':['sh','-c',
                'test "$(cat answer.txt)" = LOOP_CONTROL && test -f entrypoint-ready']},observation=observation,
            harness_runtime_identity=binding,timeout_s=60,run_id=name)
        assert not result.get('invalid') and result['exit_code']==0,result
        assert result['verdict']['passed'],result['verdict']
        row=next(json.loads(line)['runtime_control'] for line in result['trace'].splitlines()
                 if 'runtime_control' in json.loads(line))
        assert row['startup_ready'] and row['readonly']
        if state=='mount':
            assert row['entrypoint']=='present'
    finally:
        if created:
            container.remove_container(name)
        if image is not None:
            container.remove_image(tag)
