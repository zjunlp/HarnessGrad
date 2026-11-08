"""Harbor dependency custody; Docker is never started and trial APIs never run."""
import asyncio
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest
from harnessgrad import dataset_runtime, dataset_runtime_identity, verification_context
from data.scaffolds import native_harbor as native
from data.scaffolds.native_harbor_runtime import python_runtime, harbor_entry


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    """Tiny authored component to mutate; separate real-90-package proof is opt-in."""
    source = tmp_path / 'platform'
    folder = source / 'data/datasets/native_harbor'
    folder.mkdir(parents=True)
    (folder / 'execution-runtime.json').write_text(json.dumps({'schema':dataset_runtime.EXECUTION_SCHEMA,
        'dataset':'native_harbor','python':f'{sys.version_info.major}.{sys.version_info.minor}',
        'requirements':'requirements.lock'}))
    (folder / 'requirements.lock').write_text('harbor==0.23.0\ntransitive-control==1.0\n')
    monkeypatch.setattr(dataset_runtime,'ROOT',source)
    original_run=dataset_runtime._run
    def run(argv,**kwargs):
        args=list(map(str,argv))
        if args[0]!='/authored/uv':return original_run(argv,**kwargs)
        if args[1:3]==['python','find']:return str(Path(sys.executable).resolve())+'\n'
        if args[1]=='venv':
            subprocess.run([sys.executable,'-m','venv','--without-pip',args[-1]],check=True,capture_output=True)
        elif args[1]=='pip':
            venv=Path(args[args.index('--python')+1]).parent.parent
            site=venv/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
            for name,version in [('harbor','0.23.0'),('transitive_control','1.0')]:
                pkg=site/name;pkg.mkdir();(pkg/'__init__.py').write_text('VALUE=1\n')
                dist=site/(name+'-'+version+'.dist-info');dist.mkdir()
                (dist/'METADATA').write_text('Name: '+name.replace('_','-')+'\nVersion: '+version+'\n')
        else:return 'authored uv\n'
        return ''
    monkeypatch.setattr(dataset_runtime,'_run',run)
    original_which=dataset_runtime.shutil.which
    monkeypatch.setattr(dataset_runtime.shutil,'which',lambda name:'/authored/uv' if name=='uv' else original_which(name))
    store=tmp_path/'store';selected=dataset_runtime.prepare_execution('native_harbor',root=store)
    binding=dataset_runtime.bind(selected,'verifier')
    yield {'harbor_python':binding['python'],'harbor_runtime':binding},store
    for path in tmp_path.rglob('*'):
        if path.is_dir() and not path.is_symlink():path.chmod(stat.S_IMODE(path.stat().st_mode)|0o700)


def task_runtime(tmp_path,entry,monkeypatch):
    folder=tmp_path/'source/tasks/authored';folder.mkdir(parents=True)
    (folder/'instruction.md').write_text('Authored public instruction')
    (folder/'task.toml').write_text('[agent]\nnetwork_mode="no-network"\n[environment]\ndocker_image="sha256:'+('a'*64)+'"\n[verifier]\nenvironment_mode="separate"\nnetwork_mode="no-network"\n[[verifier.collect]]\ncommand="true"\n')
    row=native.inventory(tmp_path/'source','deepswe')[0]
    actor={'kind':'exec','backend':'observed-v1','image':'sha256:'+'a'*64,'python':'/usr/local/bin/python3',
           'workdir':'/app','image_workspace':'/app','network':'none','cpus':1,'memory_mb':256,'pids':64,
           'filesystems':{p:{'bytes':64<<20,'inodes':16384}for p in('/app','/tmp','/dev','/dev/shm')}}
    selected={**entry,'source_sha256':row.source_sha256,'actor':actor,
              'replay_actor_image':'sha256:'+'a'*64,'verifier_image':'sha256:'+'b'*64}
    manifest=tmp_path/'native-runtime.json';manifest.write_text(json.dumps({'schema':'harnessgrad.native-harbor-runtime.v1','tasks':{row.task_id:selected}}))
    monkeypatch.setenv('HG_DEEPSWE_RUNTIME',str(manifest))
    original=subprocess.run
    def run(argv,**kwargs):
        if argv[0]=='docker':return subprocess.CompletedProcess(argv,0,'[]','')
        return original(argv,**kwargs)
    monkeypatch.setattr(subprocess,'run',run)
    return row


def test_legacy_path_only_records_require_repreparation(tmp_path,monkeypatch):
    row=task_runtime(tmp_path,{'harbor_python':sys.executable},monkeypatch)
    with pytest.raises(native.NativeRuntimeUnavailable,match='path-only.*prepare_python'):
        native.runtime(row)


@pytest.mark.parametrize('target',['harbor/__init__.py','transitive_control/__init__.py','../pyvenv.cfg','interpreter-alias'])
def test_complete_artifact_not_metadata_controls_admission(artifact,tmp_path,monkeypatch,target):
    entry,store=artifact
    assert python_runtime.admit(entry)['runtime_id']==entry['harbor_runtime']['runtime_id']
    venv=Path(entry['harbor_runtime']['path'])
    if target=='interpreter-alias':
        alias=tmp_path/'same-base-python';alias.symlink_to(Path(sys.executable).resolve())
        path=venv/'bin/python';path.parent.chmod(0o755);path.unlink();path.symlink_to(alias)
    else:
        path=(venv/'pyvenv.cfg') if target.startswith('../') else venv/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'/target
        path.chmod(path.stat().st_mode|stat.S_IWUSR);path.write_bytes(path.read_bytes()+b'\n# same version/path; changed bytes\n')
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable,match='inventory'):
        python_runtime.admit(entry)
    assert dataset_runtime.check_execution('native_harbor',root=store)['status']=='invalid'


def test_verifier_role_flows_into_precache_and_resume_admission(artifact,tmp_path,monkeypatch):
    entry,_=artifact; row=task_runtime(tmp_path,entry,monkeypatch)
    task=native.public_task(row);spec=native.verifier(row);setup=native.setup(row)
    assert spec['python_runtime']==entry['harbor_runtime'] and spec['argv'][:3]==[entry['harbor_python'],'-I','-B']
    assert 'harbor_runtime' not in json.dumps(task)
    assert entry['harbor_python'] not in json.dumps(setup['files'])
    assert entry['harbor_runtime']['runtime_id'] not in json.dumps(setup['files'])
    identity=dataset_runtime_identity.for_evaluation(None,[task],{row.task_id:setup}, {row.task_id:spec})
    assert set(identity['tasks'][row.task_id])=={'verifier'}
    context=verification_context.capture([task],{},setups={row.task_id:setup},verifiers={row.task_id:spec})
    from harnessgrad.loops.mode_a import _identical_prior
    point={'identity':{'harness_sha':'same'},'sampling':{'trials':1},'dataset_runtime_identity':identity,'verification_context':context}
    assert _identical_prior([point],'same',1,dataset_runtime_identity=identity,verification_context=context) is point
    from harnessgrad.loops.eval_side import _resume_runtime_mismatch
    repo=tmp_path/'candidate';repo.mkdir();(repo/'harness.json').write_text(json.dumps({'name':'control','entrypoint':'agent.py'}));(repo/'agent.py').write_text('pass\n')
    prior={row.task_id:{'verification_context':context,'dataset_runtime_identity':identity}}
    assert _resume_runtime_mismatch(repo,prior,dataset_runtime_identity=identity,verification_context=context) is None
    file=Path(entry['harbor_runtime']['path'])/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/transitive_control/__init__.py'
    file.chmod(file.stat().st_mode|stat.S_IWUSR);file.write_text('VALUE=999\n')
    assert verification_context.capture([task],{},setups={row.task_id:setup},verifiers={row.task_id:spec}) == context
    assert _identical_prior([point],'same',1,dataset_runtime_identity=identity,verification_context=context) is None
    assert _resume_runtime_mismatch(repo,prior,dataset_runtime_identity=identity,verification_context=context)
    with pytest.raises(ValueError,match='artifact unavailable'):
        dataset_runtime_identity.for_evaluation(identity,[task],{row.task_id:setup}, {row.task_id:spec})
    with pytest.raises(native.NativeRuntimeUnavailable):native.runtime(row)


def test_child_refuses_binding_before_harbor_import(monkeypatch):
    calls=[]
    monkeypatch.setattr(python_runtime,'admit',lambda *a,**k:(_ for _ in ()).throw(ValueError('invalid identity')))
    import builtins
    original=builtins.__import__
    def guarded(name,*a,**k):
        if name=='harbor' or name.startswith('harbor.'):calls.append(name);pytest.fail('third-party import before admission')
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',guarded)
    with pytest.raises(ValueError,match='invalid identity'):
        asyncio.run(harbor_entry.run({'case':{'runtime':{}}}))
    assert not calls


def test_real_full_harbor_artifact_import_only(tmp_path):
    store=os.environ.get('HG_TEST_NATIVE_HARBOR_RUNTIME_ROOT')
    if not store:pytest.skip('explicit full Harbor 0.23.0 artifact store required')
    command=[sys.executable,'-I','-B','-c',
        'import sys;sys.path.insert(0,sys.argv[1]);from data.scaffolds.native_harbor_runtime.prepare_python import main;raise SystemExit(main(sys.argv[2:]))',
        str(Path(__file__).resolve().parents[2]),'probe','--runtime-root',store]
    process=subprocess.run(command,check=True,capture_output=True,text=True,timeout=90)
    result=json.loads(process.stdout)
    assert result['present'] and result['probe']['task_run'] is False
    assert result['probe']['python']==result['binding']['python']
    assert result['probe']['version']==[3,12]
    spec=dataset_runtime.execution_descriptor('native_harbor')
    assert spec['pins']['harbor']=='0.23.0' and len(spec['pins'])==90


def test_preexec_admission_failure_does_not_start_child(monkeypatch,tmp_path):
    from data.scaffolds.native_harbor_runtime import verify
    monkeypatch.setattr(python_runtime,'admit',lambda *a,**k:(_ for _ in ()).throw(ValueError('invalid runtime')))
    monkeypatch.setattr(verify.subprocess,'Popen',lambda *a,**k:pytest.fail('unadmitted child started'))
    with pytest.raises(ValueError,match='invalid runtime'):
        verify._execute_harbor({},tmp_path/'request',tmp_path/'response',{},10)


@pytest.mark.parametrize('post_drift',[False,True])
def test_real_selected_child_and_postexec_readmission(tmp_path,monkeypatch,post_drift):
    store=os.environ.get('HG_TEST_NATIVE_HARBOR_RUNTIME_ROOT')
    if not store:pytest.skip('explicit full Harbor artifact required')
    from data.scaffolds.native_harbor_runtime import verify
    selected=python_runtime.resolve(root=store)
    entry={'harbor_python':selected['python'],'harbor_runtime':selected}
    worker=tmp_path/'authored-import-only.py'
    worker.write_text('''import json,sys
from pathlib import Path
sys.path.insert(0,ROOT)
from data.scaffolds.native_harbor_runtime.python_runtime import admit
request=json.loads(Path(sys.argv[1]).read_text());entry=request['case']['runtime']
admit(entry,python=sys.executable)
from harbor.models.trial.config import TrialConfig
from harbor.trial.trial import Trial
admit(entry,python=sys.executable)
Path(sys.argv[2]).write_text(json.dumps({'harbor_runtime':entry['harbor_runtime'],
    'task_run':False,'python':sys.executable}))
'''.replace('ROOT',repr(str(Path(__file__).resolve().parents[2]))))
    request=tmp_path/'request.json';request.write_text(json.dumps({'case':{'runtime':entry}}))
    response=tmp_path/'response.json'
    actual=python_runtime.admit;calls=[]
    def admit(*a,**k):
        calls.append(1)
        if post_drift and len(calls)==2:raise dataset_runtime.DatasetRuntimeUnavailable('post-child drift')
        return actual(*a,**k)
    monkeypatch.setattr(python_runtime,'admit',admit)
    environment=dataset_runtime._child_environment()
    for key in ('HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME','TMPDIR'):
        path=tmp_path/key;path.mkdir();environment[key]=str(path)
    environment.update(PYTHON_DOTENV_DISABLED='1',LITELLM_LOCAL_MODEL_COST_MAP='True',HF_HUB_OFFLINE='1')
    if post_drift:
        with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable,match='post-child drift'):
            verify._execute_harbor(entry,request,response,environment,60,entrypoint=worker)
    else:
        result=verify._execute_harbor(entry,request,response,environment,60,entrypoint=worker)
        assert result['python']==selected['python'] and result['task_run'] is False
    assert len(calls)==2


@pytest.mark.parametrize('name',['deepswe','jobbench','swe_bench_pro'])
def test_static_profile_alias_enables_standard_h0_gate(name):
    from data.scaffolds import registry
    assert registry.execution_runtime_name(name)=='native_harbor'
    assert registry.managed_runtime(name) is True
    root=Path(__file__).resolve().parents[2]
    assert not (root/'data/datasets/native_harbor.py').exists()  # component, not adapter 57


@pytest.mark.parametrize('name',['deepswe','jobbench','swe_bench_pro'])
def test_real_h0_subprocess_captures_binding_and_refuses_missing_native_config(tmp_path,name):
    store=os.environ.get('HG_TEST_NATIVE_HARBOR_RUNTIME_ROOT')
    if not store:pytest.skip('explicit real Harbor component required for H0 control')
    root=tmp_path/'source'; task=root/'tasks/authored';task.mkdir(parents=True)
    (task/'instruction.md').write_text('Authored public instruction, no released benchmark task')
    network='public' if name=='jobbench' else 'no-network'
    (task/'task.toml').write_text(('artifacts=["/workspace/output"]\n' if name=='jobbench' else '')+'[metadata]\nsplit="main"\n[agent]\nnetwork_mode="'+network+'"\n[environment]\ndocker_image="sha256:'+('a'*64)+'"\n[verifier]\nenvironment_mode="separate"\nnetwork_mode="no-network"\n[[verifier.collect]]\ncommand="true"\n')
    row=native.inventory(root,name)[0]
    binding=python_runtime.resolve(root=store)
    workdir='/workspace' if name=='jobbench' else '/app'
    actor={'kind':'exec','backend':'native-public' if name=='jobbench' else 'observed-v1',
           'image':'sha256:'+'a'*64,'python':'/usr/local/bin/python3','workdir':workdir,'image_workspace':workdir,
           'network':network if name=='jobbench' else 'none','cpus':1,'memory_mb':256,'pids':64,
           'filesystems':{p:{'bytes':64<<20,'inodes':16384}for p in(workdir,'/tmp','/dev','/dev/shm')}}
    entry={'source_sha256':row.source_sha256,'actor':actor,'replay_actor_image':'sha256:'+'a'*64,
           'verifier_image':'sha256:'+'b'*64,'harbor_python':binding['python'],'harbor_runtime':binding}
    if name=='jobbench':
        from harnessgrad.native_judge_identity import selection
        entry.update(selection({'HG_JUDGE_HARBOR_BASE_URL':'http://127.0.0.1:9/v1',
                                'HG_JUDGE_HARBOR_MODEL':'not-contacted'}))
    if name=='swe_bench_pro':
        tooling=Path(os.environ.get('HG_TEST_SWE_PRO_TOOLING','/tmp/flash-native-upstream/swe_bench_pro/v2/tooling'))
        known=json.loads((native.RUNTIME_ASSETS/'PROVENANCE.json').read_text())['pro_tooling_sha256']
        for key,filename in [('pro_replay_source','patch_replay.py'),('pro_capture_source','locked_claude_code.py')]:
            source=tooling/filename
            if not source.is_file():pytest.skip('original-pin Pro tooling source unavailable')
            import hashlib
            assert hashlib.sha256(source.read_bytes()).hexdigest()==known[filename]
            entry[key]=str(source);entry[key+'_sha256']=known[filename]
    manifest=root/'native-runtime.json';manifest.write_text(json.dumps({'schema':'harnessgrad.native-harbor-runtime.v1','tasks':{row.task_id:entry}}))
    commands=tmp_path/'docker-metadata-only.jsonl';binaries=tmp_path/'bin';binaries.mkdir()
    docker=binaries/'docker'
    docker.write_text('#!'+sys.executable+'\nimport json,sys\nfrom pathlib import Path\n'
        +'assert sys.argv[1:3]==["image","inspect"], "only authored read-only metadata is allowed"\n'
        +'with Path('+repr(str(commands))+').open("a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nprint("[]")\n')
    docker.chmod(0o755)
    home=tmp_path/'home';home.mkdir()
    env={'PATH':str(binaries)+os.pathsep+os.defpath,'HOME':str(home),'HG_REVIEWED_ENVIRONMENT_ONLY':'1',
         'HG_DATA_'+name.upper():str(root),'HG_DATASET_RUNTIME_ROOT':store,
         'HG_NATIVE_HARBOR_ALLOW_PUBLIC':'authored H0 metadata control only',
         'HG_JUDGE_HARBOR_BASE_URL':'http://127.0.0.1:9/v1','HG_JUDGE_HARBOR_MODEL':'not-contacted',
         'PYTHONDONTWRITEBYTECODE':'1'}
    identity=dataset_runtime_identity.resolve_h0(name,side='eval',task_ids=[row.task_id],sampling='all',env=env)
    assert identity is not None
    assert identity['tasks'][row.task_id].keys()=={'verifier'}
    actual=next(iter(identity['artifacts'].values()))
    assert actual['runtime_id']==binding['runtime_id'] and actual['dataset']=='native_harbor'
    assert all(json.loads(line)[:2]==['image','inspect'] for line in commands.read_text().splitlines())
    manifest.unlink()
    with pytest.raises(ValueError,match='H0 preflight failed'):
        dataset_runtime_identity.resolve_h0(name,side='eval',task_ids=[row.task_id],sampling='all',env=env)
