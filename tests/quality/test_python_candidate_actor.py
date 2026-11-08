"""Real native actor launches from frozen, wheel-only candidate environments."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import venv
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from eval.containers import sandbox
from eval.run import runner
from harnessgrad import candidate_runtime, config
from harnessgrad import python_harness_runtime as runtime
from test_native_model_route import upstream


_ACTOR = r'''
import argparse, importlib.util, json, os, subprocess, sys, sysconfig
from pathlib import Path
from sibling import VALUE
import actor_only_control
p=argparse.ArgumentParser(); p.add_argument('--task'); p.add_argument('--workdir')
a=p.parse_args(); work=Path(a.workdir)
state=Path(os.environ['HARNESSGRAD_STATE'])/'counter'
previous=state.read_text() if state.exists() else None
state.write_text('private')
home=Path(os.environ['HOME'])/'counter'
previous_home=home.read_text() if home.exists() else None
home.write_text('private')
denied=False
try:
    Path(actor_only_control.__file__).write_text('modified')
except OSError:
    denied=True
child=subprocess.check_output(['python','-I','-B','-c','import sys; print(sys.executable)'],text=True).strip()
row={'prefix':sys.prefix,'python':sys.executable,'isolated':bool(sys.flags.isolated),
     'readonly':bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),'write_denied':denied,
     'package':actor_only_control.__file__,'package_value':actor_only_control.VALUE,
     'sibling_value':VALUE,'platform_package':importlib.util.find_spec('pytest') is not None,
     'platform_venv':Path(PLATFORM_PREFIX).exists(),'child_python':child,
     'platform_private_package':importlib.util.find_spec('platform_only_actor_probe') is not None,
     'platform_private_file':Path(PLATFORM_CANARY).exists(),
     'platform_private_package_file':Path(PLATFORM_PACKAGE_FILE).exists(),
     'base_prefix':sys.base_prefix,'stdlib':sysconfig.get_path('stdlib'),
     'base_stdlib_working':Path(json.__file__).is_file() and json.loads(json.dumps({'ok':True}))['ok'],
     'untracked_paths':{path:Path(path).exists() for path in HIDDEN_PATHS},
     'python_env':{k:v for k,v in os.environ.items() if k in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE')},
     'state_before':previous,'home_before':previous_home,'home':str(home.parent)}
(work/'answer.txt').write_text('ok')
(work/'trace.jsonl').write_text(json.dumps({'type':'authored-runtime-control','observation':row})+'\n')
'''


@pytest.fixture(scope='module')
def artifact_storage():
    if shutil.which('uv') is None or shutil.which('bwrap') is None:
        pytest.skip('real candidate controls need uv and bubblewrap')
    root=Path(tempfile.mkdtemp(prefix='hg-python-actor-',dir=Path.home()/'.cache'))
    wheelhouse=root/'wheelhouse';wheelhouse.mkdir()
    with zipfile.ZipFile(wheelhouse/'hg_actor_control-1.0-py3-none-any.whl','w') as wheel:
        files={'actor_only_control.py':'VALUE = 17\n',
            'hg_actor_control-1.0.dist-info/METADATA':'Metadata-Version: 2.1\nName: hg-actor-control\nVersion: 1.0\n',
            'hg_actor_control-1.0.dist-info/WHEEL':'Wheel-Version: 1.0\nGenerator: authored-control\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
        files['hg_actor_control-1.0.dist-info/RECORD']=''.join(name+',,\n' for name in files)+'hg_actor_control-1.0.dist-info/RECORD,,\n'
        for name,body in files.items():wheel.writestr(name,body)
    try:
        yield root
    finally:
        from harnessgrad.python_artifacts import remove_instance
        remove_instance(root)


@pytest.fixture(scope='module')
def cold_platform(artifact_storage):
    prefix = artifact_storage / 'private-platform'
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(prefix)
    site = prefix / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    package = site / 'platform_only_actor_probe.py'
    package.write_text("VALUE = 'authored platform-only canary'\n")
    canary = prefix / 'private-platform-canary.txt'
    canary.write_text('authored canary; no credentials or task data')
    code = ("import json,sys,platform_only_actor_probe; from pathlib import Path; "
            "print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
            "'package_value':platform_only_actor_probe.VALUE,'canary':Path(sys.argv[1]).read_text()}))")
    info = json.loads(subprocess.check_output([str(prefix / 'bin/python'), '-I', '-B', '-c', code,
                                               str(canary)], text=True))
    assert Path(info['prefix']) == prefix and info['prefix'] != info['base_prefix']
    assert info['package_value'] == 'authored platform-only canary'
    assert info['canary'] == 'authored canary; no credentials or task data'
    return SimpleNamespace(prefix=prefix, python=prefix / 'bin/python', package=package,
                           canary=canary, info=info)


def actor_source(platform, hidden_paths=()):
    return ('PLATFORM_PREFIX=' + repr(str(platform.prefix))
            + '\nPLATFORM_CANARY=' + repr(str(platform.canary))
            + '\nPLATFORM_PACKAGE_FILE=' + repr(str(platform.package))
            + '\nHIDDEN_PATHS=' + repr(list(hidden_paths)) + '\n' + _ACTOR)


@pytest.fixture
def actor(tmp_path, monkeypatch, artifact_storage, cold_platform):
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY','1')
    monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT',str(artifact_storage/'store'))
    monkeypatch.setenv('HG_HARNESS_WHEELHOUSE',str(artifact_storage/'wheelhouse'))
    monkeypatch.setenv('UV_CACHE_DIR',str(artifact_storage/'uv-cache'))
    monkeypatch.setenv('UV_OFFLINE','1')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(tmp_path))
    config.reset()
    repo=tmp_path/'candidate';repo.mkdir()
    manifest={'name':'authored-python-actor','version':'1','entrypoint':'agent.py','role':'control',
        'env_kinds':['files'],'runtime':{'kind':'python',
        'python':f'{sys.version_info.major}.{sys.version_info.minor}','requirements':'requirements.lock'}}
    (repo/'harness.json').write_text(json.dumps(manifest))
    (repo/'requirements.lock').write_text('hg-actor-control==1.0\n')
    (repo/'sibling.py').write_text('VALUE = 8\n')
    (repo/'agent.py').write_text(actor_source(cold_platform))

    def prepare():
        return candidate_runtime.prepare(repo)

    receipt=prepare()
    state=SimpleNamespace(repo=repo,receipt=receipt,binding=receipt['identity'],prepare=prepare,
                          tmp=tmp_path,store=artifact_storage,platform=cold_platform)
    try:
        yield state
    finally:
        config.reset()


def launch(actor, **kwargs):
    return runner.run_one(actor.repo,{'task_id':'control'},sandbox=True,
        verifier={'kind':'answer','expected':'ok'},env={'kind':'files','network':'none'},
        harness_runtime_identity=actor.binding,**kwargs)


def launch_from_cold_platform(actor):
    """Run the actual framework driver from the distinct private venv."""
    code = r"""
import contextlib,io,json,sys
from pathlib import Path
import platform_only_actor_probe
root,repo,binding=sys.argv[1:]
sys.path.insert(0,root)
from eval.run import runner
from harnessgrad import candidate_runtime
def no_per_task_build(*args,**kwargs):
    raise AssertionError('per-task build')
candidate_runtime.prepare=no_per_task_build
with contextlib.redirect_stdout(io.StringIO()):
    result=runner.run_one(Path(repo),{'task_id':'control'},sandbox=True,
        verifier={'kind':'answer','expected':'ok'},env={'kind':'files','network':'none'},
        harness_runtime_identity=json.loads(binding))
result['cold_platform_control']={'prefix':sys.prefix,'base_prefix':sys.base_prefix,
    'package_value':platform_only_actor_probe.VALUE}
print(json.dumps(result))
"""
    allowed = ('PATH','HOME','LANG','LC_ALL','LC_CTYPE','HG_REVIEWED_ENVIRONMENT_ONLY',
               'HG_AGENT_BACKEND','HG_HARNESS_RUNTIME_ROOT','HG_HARNESS_WHEELHOUSE',
               'UV_CACHE_DIR','UV_OFFLINE','HARNESSGRAD_WORK_ROOT',
               'PYTHONPATH','PYTHONHOME','PYTHONUSERBASE')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    proc = subprocess.run([str(actor.platform.python), '-I', '-B', '-c', code,
        str(Path(__file__).resolve().parents[2]), str(actor.repo), json.dumps(actor.binding)],
        env=env, text=True, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result['cold_platform_control']['prefix'] == str(actor.platform.prefix)
    assert result['cold_platform_control']['prefix'] != result['cold_platform_control']['base_prefix']
    assert result['cold_platform_control']['package_value'] == 'authored platform-only canary'
    return result


def assert_private_platform_hidden_and_base_usable(actor, row):
    assert not row['platform_package'] and not row['platform_venv']
    assert not row['platform_private_package'] and not row['platform_private_file']
    assert not row['platform_private_package_file']
    artifact = json.loads(Path(actor.binding['artifact']['manifest_path']).read_text())
    assert row['base_prefix'] == artifact['base_python']['base_prefix']
    assert row['stdlib'] == artifact['base_python']['stdlib'] and row['base_stdlib_working']
    assert row['base_prefix'] != str(actor.platform.prefix)


def observation(result):
    assert not result.get('invalid'),result
    assert result['exit_code']==0,result.get('stderr')
    return json.loads(result['trace'].splitlines()[0])['observation']


def test_actor_uses_its_own_interpreter_packages_and_owned_sibling(actor, monkeypatch):
    poison=actor.tmp/'poison';poison.mkdir()
    marker=actor.tmp/'ambient-startup-entered'
    (poison/'sitecustomize.py').write_text(f"open({str(marker)!r},'w').write('entered')")
    monkeypatch.setenv('PYTHONPATH',str(poison))
    monkeypatch.setenv('PYTHONHOME',str(poison))
    monkeypatch.setenv('PYTHONUSERBASE',str(poison))
    monkeypatch.setattr(candidate_runtime,'prepare',Mock(side_effect=AssertionError('per-task build')))
    result=launch_from_cold_platform(actor)
    row=observation(result)
    artifact=actor.binding['artifact']
    assert row['prefix']==artifact['path'] and row['python']==artifact['python']
    assert row['child_python']==artifact['python']
    assert row['isolated'] and row['readonly'] and row['write_denied']
    assert row['package_value']==17 and row['sibling_value']==8
    assert Path(row['package']).is_relative_to(artifact['path'])
    assert_private_platform_hidden_and_base_usable(actor, row)
    assert row['python_env']=={} and not marker.exists()
    assert result['harness_runtime_identity']==actor.binding
    assert result['harness_runtime']['source']=='managed-python'
    assert result['harness_runtime']['identity']==actor.binding
    assert result['verdict']['passed']


def test_nested_entrypoint_prefers_its_siblings_and_keeps_root_imports_isolated(actor, monkeypatch):
    source=actor.repo/'src';source.mkdir()
    (source/'helper.py').write_text('VALUE = 23\n')
    (actor.repo/'helper.py').write_text('VALUE = -1\n')
    entry=source/'agent.py'
    entry.write_text(r'''
import argparse, importlib.util, json, os, sys
from pathlib import Path
from helper import VALUE as NEIGHBOR
from sibling import VALUE as ROOT_VALUE
import actor_only_control
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
row={'neighbor':NEIGHBOR,'root':ROOT_VALUE,'package':actor_only_control.VALUE,
     'entry':sys.argv[0],'path':sys.path[:2],'isolated':bool(sys.flags.isolated),
     'ambient_importable':importlib.util.find_spec('ambient_only') is not None,
     'python_env':{k:v for k,v in os.environ.items() if k in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE')}}
(Path(a.workdir)/'answer.txt').write_text('ok')
(Path(a.workdir)/'trace.jsonl').write_text(json.dumps({'observation':row})+'\n')
''')
    manifest=json.loads((actor.repo/'harness.json').read_text())
    manifest['entrypoint']='src/agent.py'
    (actor.repo/'harness.json').write_text(json.dumps(manifest))
    actor.binding=actor.prepare()['identity']
    poison=actor.tmp/'poison';poison.mkdir()
    marker=actor.tmp/'ambient-startup-entered'
    (poison/'sitecustomize.py').write_text(f"open({str(marker)!r},'w').write('entered')")
    (poison/'ambient_only.py').write_text('VALUE = 999\n')
    monkeypatch.setenv('PYTHONPATH',str(poison))
    monkeypatch.setenv('PYTHONHOME',str(poison))
    monkeypatch.setenv('PYTHONUSERBASE',str(poison))
    monkeypatch.setattr(candidate_runtime,'prepare',Mock(side_effect=AssertionError('per-task build')))
    result=launch(actor)
    row=observation(result)
    assert row['neighbor']==23 and row['root']==8 and row['package']==17
    assert row['entry']==str(entry) and row['path']==[str(source),str(actor.repo)]
    assert row['isolated'] and not row['ambient_importable']
    assert row['python_env']=={} and not marker.exists()
    assert result['verdict']['passed']


def test_concurrent_tasks_share_readonly_dependencies_but_no_writable_state(actor):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:launch(actor),range(2)))
    rows=list(map(observation,results))
    assert {row['prefix'] for row in rows}=={actor.binding['artifact']['path']}
    assert len({row['home'] for row in rows})==2
    assert all(row['state_before'] is None and row['home_before'] is None for row in rows)
    assert not (actor.repo/'.state/counter').exists()
    assert all(not Path(row['home']).exists() for row in rows)


def test_runtime_under_private_tmp_restores_only_its_exact_readonly_paths(actor, monkeypatch):
    storage=Path(tempfile.mkdtemp(prefix='hg-python-actor-tmp-',dir='/tmp'))
    try:
        marker=storage/'untracked-control.txt';marker.write_text('private store neighbor')
        monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT',str(storage/'store'))
        (actor.repo/'agent.py').write_text(actor_source(actor.platform, [str(marker)]))
        actor.binding=actor.prepare()['identity']
        assert Path(actor.binding['artifact']['path']).is_relative_to(storage)
        result=launch_from_cold_platform(actor)
        row=observation(result)
        assert row['prefix']==actor.binding['artifact']['path']
        assert row['python']==row['child_python']==actor.binding['artifact']['python']
        assert row['readonly'] and row['write_denied'] and row['package_value']==17
        assert_private_platform_hidden_and_base_usable(actor, row)
        assert row['untracked_paths']=={str(marker):False}
        assert result['verdict']['passed']
    finally:
        from harnessgrad.python_artifacts import remove_instance
        remove_instance(storage)


def test_actor_environment_is_not_projected_into_the_trusted_scorer(actor):
    check="""
import importlib.util,json,os,sys
from pathlib import Path
facts={'actor_importable':importlib.util.find_spec('actor_only_control') is not None,
       'actor_runtime_visible':Path(ACTOR_PATH).exists(),'prefix':sys.prefix}
Path('verifier/evidence.json').write_text(json.dumps(facts))
Path('verifier/reward.txt').write_text('1' if not facts['actor_importable'] and not facts['actor_runtime_visible'] else '0')
""".replace('ACTOR_PATH',repr(actor.binding['artifact']['path']))
    verifier={'kind':'command','argv':[sys.executable,'-I','-B','check.py'],
        'inputs':[{'path':'check.py','content':check}],
        'reward_file':'/verifier/reward.txt','evidence_file':'/verifier/evidence.json',
        'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}}
    result=runner.run_one(actor.repo,{'task_id':'scorer-boundary'},verifier=verifier,
        env={'kind':'files','network':'none'},harness_runtime_identity=actor.binding)
    assert result['verdict'].get('score')==1,result
    facts=result['verdict']['evidence']
    assert not facts['actor_importable'] and not facts['actor_runtime_visible']
    assert facts['prefix']==sys.prefix


def test_native_model_socket_survives_the_managed_bootstrap(actor, upstream, monkeypatch):
    (actor.repo/'agent.py').write_text(r'''
import argparse,json,os,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
request=urllib.request.Request(os.environ['HG_AGENT_BASE_URL']+'/chat/completions',data=b'authored socket control',method='POST')
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:
    assert response.status==201
    reply=response.read().decode()
(Path(a.workdir)/'answer.txt').write_text(reply)
(Path(a.workdir)/'trace.jsonl').write_text(json.dumps({'type':'authored-route-control','reply':reply})+'\n')
''')
    actor.binding=actor.prepare()['identity']
    monkeypatch.setenv('HG_AGENT_BACKEND','openai')
    monkeypatch.setenv('HG_AGENT_BASE_URL',upstream[0])
    config.reset()
    result=runner.run_one(actor.repo,{'task_id':'socket'},harness_runtime_identity=actor.binding,
        verifier={'kind':'answer','expected':'authored socket control'},env={'kind':'files','network':'none'})
    assert not result.get('invalid') and result['verdict']['passed'],result
    assert len(upstream[1])==1
    assert upstream[1][0][0]=='/v1/chat/completions'


@pytest.mark.parametrize('git_kind',['directory','file'])
def test_ignored_source_storage_cannot_supply_unhashed_code(actor, git_kind):
    # The visible parent is source, while its private children are masked below.
    (actor.repo/'nested').mkdir()
    (actor.repo/'nested/keep.txt').write_text('owned directory marker\n')
    paths=['.git','_harnessgrad','.opencode-state','.state',
           'nested/.git','nested/_harnessgrad','nested/.opencode-state','nested/.state']
    (actor.repo/'agent.py').write_text('PATHS='+repr(paths)+'\nGIT_FILE='+repr(git_kind=='file')+'\n'+r'''
import argparse,importlib.machinery,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
root=Path(__file__).parent;observed={}
for relative in PATHS:
    target=root/relative
    denied=False
    try:(target/'new.py').write_text('unhashed')
    except OSError:denied=True
    text=None
    if relative=='.git' and GIT_FILE:
        try:text=target.read_text()
        except OSError:text='unreadable'
    observed[relative]={'marker':(target/'unhashed.py').exists(),
        'importable':importlib.machinery.PathFinder.find_spec('unhashed',[str(target)]) is not None,
        'write_denied':denied,'file_text':text}
(Path(a.workdir)/'answer.txt').write_text('ok')
(Path(a.workdir)/'trace.jsonl').write_text(json.dumps(observed)+'\n')
''')
    actor.binding=actor.prepare()['identity']
    for relative in paths:
        path=actor.repo/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        if relative=='.git' and git_kind=='file':
            path.write_text('unhashed Git worktree pointer')
        else:
            path.mkdir(exist_ok=True)
            (path/'unhashed.py').write_text('SECRET = True\n')
    result=launch(actor)
    assert not result.get('invalid'),result
    assert result['exit_code']==0,result.get('stderr')
    rows=json.loads(result['trace'].splitlines()[0])
    for relative,row in rows.items():
        assert not row['marker'] and not row['importable']
        assert row['write_denied'] is (relative!='.state')
    if git_kind=='file':assert rows['.git']['file_text'] in ('','unreadable')


@pytest.mark.parametrize('route',['exec-inside','exec-beside','container','unsandboxed','projection','workers','observed'])
def test_unsupported_routes_cannot_use_old_scores_or_launch(actor, monkeypatch, route):
    env={'kind':'files','network':'none'};setup={};sandboxed=True
    if route.startswith('exec') or route=='container':
        env={'kind':'exec','image':'sha256:'+'0'*64,'placement':'beside' if route.endswith('beside') else 'inside'}
        if route=='container':env['state']='container'
    elif route=='unsandboxed':sandboxed=False
    elif route=='projection':env['agent_runtime']={'package_dir':'/unread/package','files':['public.py']}
    elif route=='workers':setup['workers']=[{'id':'one','goal':'authored peer','files':[],'artifacts':[]}]
    elif route=='observed':env['backend']='observed-v1'
    launch=Mock(side_effect=AssertionError('unsupported route reached actor'))
    monkeypatch.setattr(runner,'run_one',launch)
    monkeypatch.setattr(runner,'_observations_for',Mock(side_effect=AssertionError('unsupported route probed Docker')))
    result=runner._evaluate(actor.repo,[{'task_id':'control'}],{'control':'ok'},
        cache={'same-source:control:1':{'score':1}},harness_sha='same-source',
        setups={'control':setup},envs={'control':env},sandbox=sandboxed,
        harness_runtime_identity=actor.binding)
    assert result['invalid'] and not result['per_task'] and not result['cached_tasks']
    launch.assert_not_called()


def test_cache_requires_its_binding_and_changed_source_gets_a_fresh_measurement(actor, monkeypatch):
    original=runner.run_one
    calls=Mock(wraps=original);monkeypatch.setattr(runner,'run_one',calls)
    cache={}
    def evaluate(binding):
        return runner._evaluate(actor.repo,[{'task_id':'control'}],{'control':'ok'},cache=cache,
            harness_sha='same-source',envs={'control':{'kind':'files','network':'none'}},
            harness_runtime_identity=binding)
    assert not evaluate(actor.binding)['invalid']
    assert evaluate(actor.binding)['cached_tasks']==['control'] and calls.call_count==1
    entry=next(iter(cache.values()));entry.pop('harness_runtime_identity')
    assert evaluate(actor.binding)['cached_tasks']==[] and calls.call_count==2
    next(iter(cache.values()))['harness_runtime'].pop('identity')
    assert evaluate(actor.binding)['cached_tasks']==[] and calls.call_count==3
    (actor.repo/'sibling.py').write_text('VALUE = 8\n# source revision\n')
    refused=evaluate(actor.binding)
    assert refused['invalid'] and not refused['per_task'] and calls.call_count==3
    receipt=actor.prepare()
    assert receipt['cache_hit'] and receipt['identity']['artifact']==actor.binding['artifact']
    assert receipt['identity']!=actor.binding
    assert evaluate(receipt['identity'])['cached_tasks']==[] and calls.call_count==4


def test_corrupt_runtime_cannot_bypass_admission_through_cache(actor, monkeypatch):
    cache={}
    kwargs={'envs':{'control':{'kind':'files','network':'none'}},'harness_runtime_identity':actor.binding}
    runner._evaluate(actor.repo,[{'task_id':'control'}],{'control':'ok'},cache=cache,**kwargs)
    cfg=Path(actor.binding['artifact']['path'])/'pyvenv.cfg'
    original,mode=cfg.read_bytes(),stat.S_IMODE(cfg.stat().st_mode)
    try:
        cfg.chmod(mode|stat.S_IWUSR);cfg.write_bytes(original+b'\n# changed artifact\n');cfg.chmod(mode)
        monkeypatch.setattr(runner,'run_one',Mock(side_effect=AssertionError('corrupt runtime launched')))
        result=runner._evaluate(actor.repo,[{'task_id':'control'}],{'control':'ok'},cache=cache,**kwargs)
        assert result['invalid'] and not result['per_task'] and not result['cached_tasks']
    finally:
        cfg.chmod(mode|stat.S_IWUSR);cfg.write_bytes(original);cfg.chmod(mode)


def test_legacy_runtime_path_cannot_expose_platform_packages_or_reuse_cache(actor, monkeypatch):
    manifest=json.loads((actor.repo/'harness.json').read_text())
    manifest['runtime_paths']=[sys.prefix]
    (actor.repo/'harness.json').write_text(json.dumps(manifest))
    start=Mock(side_effect=AssertionError('untracked runtime path reached actor'))
    monkeypatch.setattr(runner,'run_one',start)
    result=runner._evaluate(actor.repo,[{'task_id':'control'}],{'control':'ok'},
        cache={'same-source:control:1':{'score':1}},harness_sha='same-source',
        envs={'control':{'kind':'files','network':'none'}},harness_runtime_identity=actor.binding)
    assert not result['per_task'] and not result['cached_tasks']
    assert 'runtime_paths' in result['invalid']['control']['detail']
    start.assert_not_called()


def test_source_changed_during_task_materialization_is_refused_before_launch(actor, monkeypatch):
    materialize=runner._materialize
    def change(*args,**kwargs):
        result=materialize(*args,**kwargs)
        (actor.repo/'sibling.py').write_text('VALUE = 99\n')
        return result
    monkeypatch.setattr(runner,'_materialize',change)
    start=Mock(side_effect=AssertionError('changed candidate reached process launch'))
    monkeypatch.setattr(runner.sandbox_mod,'run',start)
    result=launch(actor,setup={'files':[{'path':'public-control.txt','content':'authored input'}]})
    assert result['invalid']['stage']=='harness runtime identity'
    assert 'source changed' in result['invalid']['detail']
    start.assert_not_called()


def test_sandbox_opt_out_suppresses_only_automatic_platform_projection(tmp_path, monkeypatch):
    repo=tmp_path/'repo';repo.mkdir();task=tmp_path/'task';task.mkdir()
    probe=Mock(side_effect=AssertionError('platform interpreter probed'))
    monkeypatch.setattr(sandbox,'_interpreter_mounts',probe)
    monkeypatch.setattr(sandbox,'_interpreter_symlinks',probe)
    plan=sandbox.command(['/usr/bin/python3','-I','-c','pass'],platform=Path(__file__).resolve().parents[2],
        work_root=tmp_path,repo=repo,task_dir=task,env={'PATH':os.defpath},platform_interpreter=False)
    assert '--ro-bind' in plan
    assert '/usr' in plan
    probe.assert_not_called()
