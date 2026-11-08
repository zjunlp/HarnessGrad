"""Actual mini CLI/LiteLLM, isolated actors and task environments; no model calls."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import venv

import pytest

from eval.containers import container
from eval.run import runner
from eval.service import container_observation
from harnessgrad import candidate_runtime, config, python_exec_runtime as runtime
from harnessgrad import python_harness_runtime as native

ROOT=Path(__file__).resolve().parents[2]
HARNESS=ROOT/'base_harness/miniswe'


@pytest.fixture(scope='module')
def miniswe_store():
    if not shutil.which('uv') or not shutil.which('bwrap'):
        pytest.skip('MiniSWE integration requires uv and bubblewrap')
    provided=os.environ.get('HG_TEST_MINISWE_RUNTIME_ROOT')
    owned=None
    if provided:
        store=Path(provided).resolve(strict=True)
    else:
        cache=Path.home()/'.cache';cache.mkdir(parents=True,exist_ok=True)
        owned=Path(tempfile.mkdtemp(prefix='hg-miniswe-test-',dir=cache))
        store=owned/'store'
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY','1')
            patch.setenv('UV_OFFLINE','1')
            patch.setenv('UV_PYTHON_DOWNLOADS','never')
            config.reset()
            try:
                prepared=runtime.prepare(HARNESS,root=store,build=not bool(provided))
            except native.RuntimeUnavailable as exc:
                if not provided and any(s in str(exc) for s in ('not found in the cache','not cached','No interpreter')):
                    pytest.skip('prepare MiniSWE locked wheels first: '+str(exc))
                raise
        yield SimpleNamespace(store=store,identity=prepared['identity'])
    finally:
        config.reset()
        if owned is not None:native.remove_instance(owned)


@pytest.fixture
def managed_miniswe(miniswe_store,monkeypatch,tmp_path):
    for key in list(os.environ):
        if key.startswith(('HG_AGENT_','HG_METHOD_','HG_HARNESS_')) and key!='HG_HARNESS_WHEELHOUSE':monkeypatch.delenv(key)
    # Authored local mock traffic must not consume ambient operator proxies.
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
                'http_proxy','https_proxy','all_proxy','no_proxy'):
        monkeypatch.delenv(key,raising=False)
    for key,value in {'HG_REVIEWED_ENVIRONMENT_ONLY':'1','HG_HARNESS_RUNTIME_ROOT':str(miniswe_store.store),
        'HG_AGENT_BACKEND':'mock','HG_MINISWE_STEP_LIMIT':'4','UV_OFFLINE':'1',
        'HARNESSGRAD_WORK_ROOT':str(tmp_path)}.items():monkeypatch.setenv(key,value)
    # A private platform prefix is distinct from the shared base interpreter.
    # Execute the actual driver here; a canary alone in an unused venv proves nothing.
    with tempfile.TemporaryDirectory(prefix='hg-miniswe-platform-',dir=Path.home()/'.cache') as directory:
        platform=Path(directory)/'venv'
        venv.EnvBuilder(with_pip=False).create(platform)
        site=next(platform.glob('lib/python*/site-packages'))
        (site/'hg_miniswe_platform_canary.py').write_text("VALUE='private-platform-canary'\n")
        (platform/'private-platform-canary.txt').write_text('private-platform-canary')
        probe=subprocess.run([str(platform/'bin/python'),'-I','-B','-c',
            "import sys,hg_miniswe_platform_canary;assert sys.prefix!=sys.base_prefix;"
            "assert hg_miniswe_platform_canary.VALUE=='private-platform-canary'"],
            capture_output=True,text=True,timeout=30)
        assert probe.returncode==0,probe.stdout+probe.stderr
        monkeypatch.setenv('HG_MINISWE_TEST_PLATFORM',str(platform))
        config.reset()
        try:yield miniswe_store
        finally:config.reset()


@pytest.fixture(scope='module')
def miniswe_images(miniswe_store):
    if container.available() is None:pytest.skip('MiniSWE image controls require local Docker')
    try:
        images={minor:container.resolve_digest(tag) for minor,tag in
            [('3.11','python:3.11-slim'),('3.12','python:3.12.8-slim'),
             ('3.13','python:3.13-slim'),('absent','ubuntu:24.04')]}
    except container.ContainerUnavailable as exc:pytest.skip('import the control images first: '+str(exc))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY','1')
        patch.setenv('HG_HARNESS_RUNTIME_ROOT',str(miniswe_store.store))
        patch.setenv('UV_OFFLINE','1')
        config.reset()
        runtime.prepare(HARNESS,envs={key:{'kind':'exec','image':image} for key,image in images.items()},build=True)
    return images


@pytest.fixture(scope='module')
def miniswe_equals_store(miniswe_images):
    owned=Path(tempfile.mkdtemp(prefix='hg-miniswe-path=',dir=Path.home()/'.cache'))
    store=owned/'runtime=equals with spaces'
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY','1')
            patch.setenv('UV_OFFLINE','1')
            config.reset()
            runtime.prepare(HARNESS,root=store,envs={key:{'kind':'exec','image':miniswe_images[key]}
                for key in ('3.12','absent')},build=True)
        yield store
    finally:
        config.reset()
        native.remove_instance(owned)


PROBE='''
import importlib.metadata as _meta,importlib.util as _util,json as _json,os as _os,sys as _sys
from pathlib import Path as _Path
_work=_Path(_sys.argv[_sys.argv.index('--workdir')+1])
_mini=_Path(_util.find_spec('minisweagent').origin)
_old=[]
for _p in (_Path(_os.environ['HOME'])/'counter',_Path(_os.environ['HARNESSGRAD_STATE'])/'counter'):
 _old.append(_p.exists());_p.write_text('private')
try:
 _mini.write_text('unexpected mutation');_denied=False
except OSError:_denied=True
(_work/'actor-probe.json').write_text(_json.dumps({
 'python':_sys.executable,'prefix':_sys.prefix,'isolated':bool(_sys.flags.isolated),
 'mini_package':str(_mini),'lite_version':_meta.version('litellm'),
 'readonly':bool(_os.statvfs(_sys.prefix).f_flag & _os.ST_RDONLY),'write_denied':_denied,
 'platform_visible':_Path(PLATFORM_PREFIX).exists(),
 'platform_package_visible':_util.find_spec('hg_miniswe_platform_canary') is not None,
 'platform_canary_visible':(_Path(PLATFORM_PREFIX)/'private-platform-canary.txt').exists(),
 'base_prefix':_sys.base_prefix,'pytest_visible':_util.find_spec('pytest') is not None,
 'private_visible':(_work/'private-token.txt').exists(),'previous':_old,
 'task_module_visible':_util.find_spec('hg_loop_image_dependency') is not None,
 'task_path_module_visible':_util.find_spec('hg_loop_path_dependency') is not None}))
'''


def instrumented(tmp_path,*,task_dependencies=False):
    source=tmp_path/'source';shutil.copytree(HARNESS,source)
    entry=source/'agent.py'
    text=entry.read_text().replace('from __future__ import annotations\n','from __future__ import annotations\n'+PROBE.replace(
        'PLATFORM_PREFIX',repr(os.environ['HG_MINISWE_TEST_PLATFORM'])),1)
    text=text.replace('    finally:\n        if server is not None:',
        '''    finally:
        _row=_json.loads((_work/'actor-probe.json').read_text())
        _row['task']=_json.loads((_work/'task-probe.json').read_text()) if (_work/'task-probe.json').exists() else {'missing':True}
        if _row['task'].get('missing') and (_work/'native-mini-log.txt').exists():_row['native_log']=(_work/'native-mini-log.txt').read_text()[-12000:]
        with (_work/'trace.jsonl').open('a') as _trace:_trace.write(_json.dumps({'miniswe_runtime_control':_row})+'\\n')
        if server is not None:''')
    # Keep failure diagnostics only in this authored control, with synthetic keys.
    text=text.replace('                summary = write_trace(trajectory, record)',
        "                (workdir/'native-mini-log.txt').write_text(log_path.read_text())\n                summary = write_trace(trajectory, record)")
    entry.write_text(text)
    probe='''import importlib.metadata as m,json,os,sys
from pathlib import Path
previous=[]
for p in (Path(os.environ['HOME'])/'tool-counter',Path(os.environ['MSWEA_GLOBAL_CONFIG_DIR'])/'tool-counter'):
 previous.append(p.exists());p.write_text('private')
try: mini=m.version('mini-swe-agent')
except m.PackageNotFoundError: mini=None
row={'python':sys.executable,'prefix':sys.prefix,'path':os.environ.get('PATH'),
 'pythonpath':os.environ.get('PYTHONPATH'),'virtual_env':os.environ.get('VIRTUAL_ENV'),
 'mini_distribution':mini,'previous':previous,'home':os.environ['HOME'],
 'mini_config':os.environ['MSWEA_GLOBAL_CONFIG_DIR']}
'''
    if task_dependencies:
        probe+="import hg_loop_image_dependency as dep,hg_loop_path_dependency as extra; row.update(dependency=dep.VALUE,extra=extra.VALUE)\n"
    probe+="print(json.dumps(row))\n"
    command=("set -e; if command -v python >/dev/null; then python -B -c "+shlex.quote(probe)
        +" > task-probe.json; else printf '%s' '{\"python\":null,\"previous\":[false,false]}' > task-probe.json; fi; "
        +("hg-loop-task-tool > task-tool.txt; python3 -B -c "+shlex.quote('import hg_loop_image_dependency,hg_loop_path_dependency')+'; ' if task_dependencies else '')
        +"printf 'MINISWE_MOCK\\n' > answer.txt")
    mock=source/'mock_model.py';lines=mock.read_text().splitlines(keepends=True)
    assignment=next(n for n in ast.parse(''.join(lines)).body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='SCRIPT' for t in n.targets))
    lines[assignment.lineno-1:assignment.end_lineno]=['SCRIPT = '+repr((command,'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'))+'\n']
    mock.write_text(''.join(lines))
    return source


POISON=[{'path':'minisweagent/__init__.py','content':'raise RuntimeError("task package replaced mini CLI")\n'},
        {'path':'mini.yaml','content':'agent: {step_limit: "task config replaced native config"}\n'}]


def _cold_run_one(repo,task,**kwargs):
    platform=Path(os.environ['HG_MINISWE_TEST_PLATFORM'])
    with tempfile.TemporaryDirectory(prefix='hg-miniswe-driver-') as directory:
        directory=Path(directory)
        request,output=directory/'request.json',directory/'result.json'
        request.write_text(json.dumps({'repo':str(repo),'task':task,'kwargs':kwargs}))
        code="""import importlib.util,json,sys
from pathlib import Path
import hg_miniswe_platform_canary
assert sys.prefix!=sys.base_prefix
assert hg_miniswe_platform_canary.VALUE=='private-platform-canary'
for name in ('pip','pytest','minisweagent','litellm','openai','pyarrow'):
    assert importlib.util.find_spec(name) is None,name
sys.path.insert(0,sys.argv[1])
from eval.run.runner import run_one
request=json.loads(Path(sys.argv[2]).read_text())
result=run_one(Path(request['repo']),request['task'],**request['kwargs'])
Path(sys.argv[3]).write_text(json.dumps(result))
"""
        completed=subprocess.run([str(platform/'bin/python'),'-I','-B','-c',code,
            str(ROOT),str(request),str(output)],capture_output=True,text=True,timeout=180)
        assert completed.returncode==0,completed.stdout+completed.stderr
        assert (platform/'private-platform-canary.txt').read_text()=='private-platform-canary'
        return json.loads(output.read_text())


def launch(source,env,*,task_id='controlled',setup=None):
    binding=candidate_runtime.prepare(source,envs={task_id:env})['identity']
    artifact=runtime.placement(binding,env)['artifact']
    observed=(container_observation.observe_for(env['image'],env,network='none',
        read_only=env.get('state')!='container').receipt() if env['kind']=='exec' else None)
    verifier={'kind':'command','argv':['sh','-c',
        'test "$(cat answer.txt)" = MINISWE_MOCK && test "$(cat private-token.txt)" = PRIVATE_CONTROL && ! test -x '+shlex.quote(artifact['python'])],
        'inputs':[{'path':'private-token.txt','content':'PRIVATE_CONTROL'}]}
    result=_cold_run_one(source,{'task_id':task_id,'goal':'Write a short answer.txt.'},
        env=env,setup=setup or {'files':POISON},verifier=verifier,observation=observed,
        harness_runtime_identity=binding,timeout_s=90,run_id='miniswe-managed-'+task_id)
    assert not result.get('invalid') and result['exit_code']==0,result
    assert result['verdict']['passed'],result['verdict']
    rows=[json.loads(line) for line in result['trace'].splitlines()]
    row=next(r['miniswe_runtime_control'] for r in rows if 'miniswe_runtime_control' in r)
    assert row['python']==artifact['python'] and row['prefix']==artifact['path']
    assert row['isolated'] and row['readonly'] and row['write_denied']
    assert not any(row[k] for k in ('platform_visible','platform_package_visible','platform_canary_visible','pytest_visible','private_visible','task_module_visible','task_path_module_visible')),row
    assert row['previous']==[False,False] and row['task']['previous']==[False,False]
    assert Path(row['mini_package']).is_relative_to(artifact['path']) and row['lite_version']=='1.75.9'
    assert [r for r in rows if r.get('submitted')][-1]['mini_version']=='2.4.6'
    assert next(r['usage']['calls'] for r in rows if 'usage' in r)==2
    observations=[r for r in rows if 'observation' in r]
    assert observations and observations[0]['observation']['returncode']==0
    # Upstream's submitted sentinel is retained as an unexecuted tool action.
    assert all(r['observation']['returncode']==0 or
        (r['command']=='echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' and r['observation']['returncode']==-1
         and r.get('error')=='action was not executed')
        for r in observations)
    identity=rows[0]['harness_identity']
    assert Path(identity['config']['config']).is_relative_to(artifact['path'])
    assert 'PRIVATE_CONTROL' not in result['trace']
    return result,row,binding


@pytest.mark.parametrize('minor,state',[('files','mount'),*[(minor,state)
    for minor in ('3.11','3.12','3.13','absent') for state in ('mount','container')]])
def test_native_cli_uses_bound_runtime_and_keeps_task_python(managed_miniswe,tmp_path,minor,state,request):
    miniswe_images=request.getfixturevalue('miniswe_images') if minor!='files' else {}
    source=instrumented(tmp_path)
    env=({'kind':'files','network':'none'} if minor=='files' else
        {'kind':'exec','image':miniswe_images[minor],'workdir':'/work','network':'none'})
    if minor!='files' and state=='container':env['state']='container'
    result,row,binding=launch(source,env)
    (tmp_path/'result.json').write_text(json.dumps(result,indent=2))
    task=row['task'];artifact=runtime.placement(binding,env)['artifact']
    if minor=='files':assert task['python']==artifact['python'] and task['mini_distribution']=='2.4.6'
    elif minor=='absent':assert task['python'] is None
    else:
        assert task['python']!=artifact['python'] and task['mini_distribution'] is None
        assert task['prefix']=='/usr/local'
        assert result['harness_runtime']['python']==minor


@pytest.mark.parametrize('minor,state',[
    ('files','mount'),('3.12','mount'),('3.12','container'),
    ('absent','mount'),('absent','container')])
def test_native_cli_loads_bound_config_from_runtime_path_with_equals(
        managed_miniswe,miniswe_equals_store,miniswe_images,tmp_path,monkeypatch,minor,state):
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT',str(miniswe_equals_store))
    config.reset()
    source=instrumented(tmp_path)
    env=({'kind':'files','network':'none'} if minor=='files' else
        {'kind':'exec','image':miniswe_images[minor],'workdir':'/work','network':'none'})
    if minor!='files' and state=='container':env['state']='container'
    # launch() also supplies task-owned mini.yaml and minisweagent package poison.
    result,_,binding=launch(source,env)
    (tmp_path/'result.json').write_text(json.dumps(result,indent=2))
    artifact=runtime.placement(binding,env)['artifact']
    (tmp_path/'artifact.json').write_text(Path(artifact['manifest_path']).read_text())
    assert '=equals with spaces' in artifact['path']
    identity=json.loads(result['trace'].splitlines()[0])['harness_identity']
    assert Path(identity['config']['config']).is_relative_to(artifact['path'])


def test_empty_platform_can_check_prepared_miniswe_without_pip_or_sdks(managed_miniswe,tmp_path):
    platform=tmp_path/'platform';venv.EnvBuilder(with_pip=False).create(platform)
    code='''import importlib.util,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
for name in ('pip','minisweagent','litellm','openai','pyarrow'):assert importlib.util.find_spec(name) is None,name
from harnessgrad import candidate_runtime
r=candidate_runtime.prepare(Path(sys.argv[2]))
assert r['cache_hit'] and not r['installed']
assert candidate_runtime.admit(Path(sys.argv[2]),r['identity'])==r['identity']
print(json.dumps(r))
'''
    result=subprocess.run([str(platform/'bin/python'),'-I','-B','-c',code,str(ROOT),str(HARNESS)],
        env=dict(os.environ),capture_output=True,text=True,timeout=120)
    (tmp_path/'empty-platform.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stdout+result.stderr


# Reuse the authored image fixture that provides both system and venv task Python,
# a PYTHONPATH-only module, and a custom tool directory. It never pulls an image.
from test_loop_managed_runtime import task_python_image

@pytest.fixture(scope='module')
def loop_images(miniswe_images):return {'python':miniswe_images['3.12'],'absent':miniswe_images['absent']}


@pytest.mark.parametrize('state',['mount','container'])
def test_task_image_configuration_survives_the_native_cli(managed_miniswe,task_python_image,tmp_path,state):
    source=instrumented(tmp_path,task_dependencies=True)
    env={'kind':'exec','image':task_python_image['image'],'workdir':'/work','network':'none'}
    if state=='container':env['state']='container'
    result,row,_=launch(source,env)
    (tmp_path/'result.json').write_text(json.dumps(result,indent=2))
    task=row['task']
    assert task['path']==task_python_image['path']
    assert task['prefix']==task_python_image['prefix'] and task['pythonpath']=='/opt/hg-loop-task-path'
    assert task['virtual_env']==(task_python_image['prefix'] if task_python_image['venv'] else None)
    assert task['dependency']=='IMAGE_DEPENDENCY_OK' and task['extra']=='IMAGE_PATH_OK'
    assert task['mini_distribution'] is None


@pytest.mark.parametrize('kind',['files','exec'])
def test_repeated_tasks_share_dependencies_without_mini_state(managed_miniswe,miniswe_images,tmp_path,kind):
    source=instrumented(tmp_path)
    env={'kind':'files','network':'none'} if kind=='files' else {'kind':'exec','image':miniswe_images['3.11'],'workdir':'/work','network':'none'}
    first,a,one=launch(source,env,task_id='state-one')
    second,b,two=launch(source,env,task_id='state-two')
    assert one==two and a['prefix']==b['prefix']
    if kind=='files':assert a['task']['home']!=b['task']['home']
    (tmp_path/'results.json').write_text(json.dumps([first,second],indent=2))


def test_shipped_h0_cache_snapshot_eval_and_resume(managed_miniswe,tmp_path,request):
    ids=tmp_path/'ids.txt';ids.write_text('t01\n')
    runs,work=tmp_path/'runs',tmp_path/'work'
    env=dict(os.environ,HG_AGENT_MODEL='miniswe-mock',HG_AGENT_BASE_URL='')
    platform=Path(os.environ['HG_MINISWE_TEST_PLATFORM'])
    platform_python=platform/'bin/python'
    def call(argv,label):
        code="""import importlib.util,runpy,sys
import hg_miniswe_platform_canary
assert sys.prefix!=sys.base_prefix
assert hg_miniswe_platform_canary.VALUE=='private-platform-canary'
for name in ('pip','pytest','minisweagent','litellm','openai','pyarrow'):
    assert importlib.util.find_spec(name) is None,name
sys.path.insert(0,sys.argv[1])
script=sys.argv[2]
sys.argv=sys.argv[2:]
runpy.run_path(script,run_name='__main__')
"""
        result=subprocess.run([str(platform_python),'-I','-B','-c',code,str(ROOT),*map(str,argv)],cwd=ROOT,env=env,
            capture_output=True,text=True,timeout=240)
        assert (platform/'private-platform-canary.txt').read_text()=='private-platform-canary'
        (tmp_path/(label+'.log')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,result.stdout+result.stderr
        return result
    h0=[ROOT/'tools/prepare_h0.py','--dataset','demo','--harness','miniswe','--study-ids',ids,
        '--jobs','1','--model','miniswe-mock','--endpoint','','--runs-root',runs]
    call(h0,'h0-create')
    donor=next(p for p in runs.glob('h0-demo-*') if p.is_dir())
    request.addfinalizer(lambda:shutil.rmtree(Path('/tmp/hg-h0-work')/donor.name,ignore_errors=True))
    before=(donor/'curve.jsonl').read_bytes()
    assert 'reusing' in call(h0,'h0-reuse').stdout
    assert (donor/'curve.jsonl').read_bytes()==before
    common=[ROOT/'driver.py','--harness','miniswe','--dataset','demo','--sampling','all',
        '--jobs','1','--trials','1','--no-summary','--runs-root',runs,'--work-root',work]
    train=[*common,'--tasks','t01','--run-id','study','--rounds','1','--reuse-identical',
        '--method-entrypoint',str(platform_python)+' '+str(ROOT/'methods/noop/run.py'),'--h0-from',donor.name]
    call(train,'train');study=runs/'study'
    points=[json.loads(line) for line in (study/'curve.jsonl').read_text().splitlines()]
    binding=points[0]['harness_runtime_identity']
    assert points[0]['shared_from']['run_id']==donor.name
    assert all(p['identity']['agent_model']=='miniswe-mock' and 'agent_model_conflict' not in p['identity'] for p in points)
    assert all(p['harness_runtime_identity']==binding and p['n_invalid']==0 and p['n_harness_failed']==0 for p in points)
    assert points[1].get('cached_tasks') or points[1].get('shared_from') or points[1].get('identical_to')
    for n in (0,1):shutil.rmtree(study/f'state_after_round{n}')
    before=(study/'curve.jsonl').read_bytes()
    call([*train,'--rounds','2','--resume'],'train-resume')
    assert (study/'curve.jsonl').read_bytes().startswith(before)
    eval_args=[*common,'--side','eval','--from-run','study','--from-round','2',
               '--tasks','t05','--rounds','0','--run-id','exam']
    call(eval_args,'eval');exam=runs/'exam'
    point=json.loads((exam/'curve.jsonl').read_text())
    assert point['harness_runtime_identity']==binding and point['n_invalid']==0
    progress={p:p.read_bytes() for p in (exam/'progress').rglob('*') if p.is_file()}
    assert progress
    call([*eval_args,'--resume'],'eval-resume')
    resumed=json.loads((exam/'curve.jsonl').read_text())
    assert resumed['harness_runtime_identity']==binding and resumed['per_task']==point['per_task']
    assert all(p.read_bytes()==raw for p,raw in progress.items())


def test_container_checkpoint_keeps_exact_artifacts_across_new_publications_and_eval_images(
        managed_miniswe,miniswe_images,tmp_path,monkeypatch):
    import driver
    from data.scaffolds import registry
    old_envs=registry.load_envs
    def envs(name,*,task_ids=None):
        if name=='demo':
            ids=task_ids or [f't{i:02}' for i in range(1,9)]
            return {tid:{'kind':'exec','image':miniswe_images['3.13' if int(tid[1:])>=5 else '3.11'],
                         'workdir':'/work','network':'none'} for tid in ids}
        return old_envs(name,task_ids=task_ids)
    monkeypatch.setattr(registry,'load_envs',envs)
    def observations(envs,tasks,*,run_id=''):
        return {task['task_id']:container_observation.observe_for(envs[task['task_id']]['image'],
            envs[task['task_id']],network='none',read_only=True,run_id=run_id).receipt() for task in tasks}
    # Existing production admission still auto-observes only service environments.
    monkeypatch.setattr(runner,'_observations_for',observations)
    monkeypatch.setenv('HG_AGENT_MODEL','miniswe-mock')
    monkeypatch.setenv('HG_AGENT_BASE_URL','')
    runs,work=tmp_path/'runs',tmp_path/'work'
    common=['--harness','miniswe','--dataset','demo','--sampling','all','--jobs','1',
            '--trials','1','--no-summary','--runs-root',str(runs),'--work-root',str(work)]
    train=[*common,'--tasks','t01','--run-id','image-study','--rounds','1','--reuse-identical',
           '--method-entrypoint',sys.executable+' '+str(ROOT/'methods/noop/run.py')]
    def call(argv):
        monkeypatch.setattr(sys,'argv',['driver.py',*argv]);assert driver.main()==0
    call(train);study=runs/'image-study'
    points=[json.loads(line) for line in (study/'curve.jsonl').read_text().splitlines()]
    binding=points[0]['harness_runtime_identity']
    assert miniswe_images['3.11'] in binding['images'] and miniswe_images['3.13'] not in binding['images']
    assert all(p['n_invalid']==0 and p['n_harness_failed']==0 and p['harness_runtime_identity']==binding for p in points)
    assert points[1].get('cached_tasks') or points[1].get('shared_from') or points[1].get('identical_to')
    for n in (0,1):shutil.rmtree(study/f'state_after_round{n}')
    before=(study/'curve.jsonl').read_bytes()
    call([*train,'--rounds','2','--resume'])
    assert (study/'curve.jsonl').read_bytes().startswith(before)
    current=[json.loads(line) for line in (study/'curve.jsonl').read_text().splitlines()]
    assert current[-1]['harness_runtime_identity']==binding and current[-1]['n_invalid']==0
    newer=runtime.prepare(HARNESS,envs={'t':envs('demo',task_ids=['t01'])['t01']},build=True,force=True)['identity']
    assert newer['native']['artifact']!=binding['native']['artifact']
    exam=[*common,'--side','eval','--from-run','image-study','--from-round','2',
          '--tasks','t05','--rounds','0','--run-id','image-exam']
    call(exam);output=runs/'image-exam'
    point=json.loads((output/'curve.jsonl').read_text());evaluated=point['harness_runtime_identity']
    assert evaluated['native']==binding['native']
    assert all(evaluated['images'][key]==value for key,value in binding['images'].items())
    assert miniswe_images['3.13'] in evaluated['images']
    assert point['evaluated_from']['harness_runtime_identity']==binding and point['n_invalid']==0
    progress={p:p.read_bytes() for p in (output/'progress').rglob('*') if p.is_file()}
    monkeypatch.setattr(runner,'run_one',lambda *a,**k:pytest.fail('completed eval resume launched an actor'))
    call([*exam,'--resume'])
    resumed=json.loads((output/'curve.jsonl').read_text())
    assert resumed['harness_runtime_identity']==evaluated and resumed['evaluated_from']==point['evaluated_from']
    assert resumed['per_task']==point['per_task'] and all(p.read_bytes()==raw for p,raw in progress.items())
