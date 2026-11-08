"""Authored selected-judge custody; no judge/model, Trial or Docker is run."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from harnessgrad import native_judge_identity as judge, dataset_runtime_identity as identity, verification_context
from data.scaffolds import native_harbor as native
from data.scaffolds.native_harbor_runtime import verify, harbor_entry
from test_native_harbor_python_identity import artifact


@pytest.fixture
def job(artifact, tmp_path, monkeypatch):
    dependency, store = artifact
    monkeypatch.setenv('HG_JUDGE_HARBOR_BASE_URL','http://127.0.0.1:9/v1')
    monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','judge-A')
    monkeypatch.setenv('HG_JUDGE_HARBOR_API_KEY','RAW_KEY_CANARY')
    source=tmp_path/'job-source';task=source/'tasks/authored';task.mkdir(parents=True)
    (task/'instruction.md').write_text('Authored public instruction')
    (task/'task.toml').write_text('artifacts=["/workspace/output"]\n[metadata]\nsplit="main"\n[agent]\nnetwork_mode="no-network"\n[environment]\ndocker_image="sha256:'+('a'*64)+'"\n[verifier]\nenvironment_mode="separate"\nnetwork_mode="no-network"\n')
    row=native.inventory(source,'jobbench')[0]
    actor={'kind':'exec','backend':'observed-v1','image':'sha256:'+'a'*64,'python':'/usr/local/bin/python3',
           'workdir':'/workspace','network':'none','cpus':1,'memory_mb':256,'pids':64,
           'filesystems':{p:{'bytes':64<<20,'inodes':16384} for p in ('/workspace','/tmp','/dev','/dev/shm')}}
    entry={**dependency,**judge.selection(),'source_sha256':row.source_sha256,'actor':actor,
           'replay_actor_image':'sha256:'+'a'*64,'verifier_image':'sha256:'+'b'*64}
    manifest=source/'native-runtime.json'
    manifest.write_text(json.dumps({'schema':'harnessgrad.native-harbor-runtime.v1','tasks':{row.task_id:entry}}))
    monkeypatch.setenv('HG_JOBBENCH_RUNTIME',str(manifest))
    original=subprocess.run
    inspections=[]
    def run(argv,**kwargs):
        if argv[0]=='docker':
            assert argv[1:3]==['image','inspect'];inspections.append(argv)
            return subprocess.CompletedProcess(argv,0,'[]','')
        return original(argv,**kwargs)
    monkeypatch.setattr(subprocess,'run',run)
    return SimpleNamespace(row=row,entry=entry,source=source,manifest=manifest,store=store,inspections=inspections)


def capture(job, monkeypatch):
    from data.scaffolds import registry
    monkeypatch.setattr(registry,'provider_runtime',lambda name:None)
    monkeypatch.setattr(registry,'provider',lambda name:None)
    task=native.public_task(job.row);spec=native.verifier(job.row)
    selected=identity.capture('jobbench',[job.row.task_id],{},{job.row.task_id:spec})
    context=verification_context.capture([task],{},verifiers={job.row.task_id:spec})
    return task,spec,selected,context


def test_selection_never_reads_or_hashes_credentials():
    class Environment:
        def get(self,key):
            assert key in {'HG_JUDGE_HARBOR_BASE_URL','HG_JUDGE_HARBOR_MODEL'}
            return {'HG_JUDGE_HARBOR_BASE_URL':'http://127.0.0.1:80/v1/', 'HG_JUDGE_HARBOR_MODEL':' A '}[key]
    selected=judge.selection(Environment())
    assert selected==judge.selection({'HG_JUDGE_HARBOR_BASE_URL':'http://127.0.0.1/v1', 'HG_JUDGE_HARBOR_MODEL':'A','HG_JUDGE_HARBOR_API_KEY':'NEVER_READ'})
    assert selected['judge_base_url']=='http://127.0.0.1/v1'
    assert 'endpoint_sha256' not in judge.public_summary(selected['judge_identity'])


@pytest.mark.parametrize('url',['https://127.0.0.1/v1','http://localhost/v1','http://192.0.2.1/v1',
 'http://u:RAW_KEY_CANARY@127.0.0.1/v1','http://127.0.0.1/v1?token=RAW_KEY_CANARY','http://127.0.0.1/#RAW_KEY_CANARY','http://127.0.0.1/?','http://127.0.0.1/#'])
def test_credential_bearing_or_nonlocal_endpoints_are_refused(url):
    with pytest.raises(ValueError) as error:judge.selection({'HG_JUDGE_HARBOR_BASE_URL':url,'HG_JUDGE_HARBOR_MODEL':'A'})
    assert 'RAW_KEY_CANARY' not in str(error.value)


def test_key_rotation_preserves_case_context_and_reuse(job,monkeypatch):
    task,spec,selected,context=capture(job,monkeypatch)
    point={'identity':{'harness_sha':'same'},'sampling':{'trials':1},'dataset_runtime_identity':selected,'verification_context':context}
    monkeypatch.setenv('HG_JUDGE_HARBOR_API_KEY','ROTATED_KEY_CANARY')
    _,again,runtime_again,context_again=capture(job,monkeypatch)
    assert spec==again and context==context_again and selected==runtime_again
    from harnessgrad.loops.mode_a import _identical_prior
    assert _identical_prior([point],'same',1,dataset_runtime_identity=selected,verification_context=context) is point
    assert 'KEY_CANARY' not in json.dumps(spec)
    assert spec['native_judge_identity']==job.entry['judge_identity']


@pytest.mark.parametrize('field,value',[('HG_JUDGE_HARBOR_MODEL','judge-B'),('HG_JUDGE_HARBOR_BASE_URL','http://127.0.0.1:10/v1')])
def test_change_requires_reprepare_then_changes_context_and_refuses_old_point(job,monkeypatch,field,value):
    task,old,selected,context=capture(job,monkeypatch)
    point={'identity':{'harness_sha':'same'},'sampling':{'trials':1},'dataset_runtime_identity':selected,'verification_context':context}
    count=len(job.inspections);monkeypatch.setenv(field,value)
    with pytest.raises(native.NativeRuntimeUnavailable,match='changed'):native.verifier(job.row)
    assert len(job.inspections)==count
    with pytest.raises(ValueError,match='changed'):identity.for_evaluation(selected,[task],{},{job.row.task_id:old})
    with pytest.raises(ValueError,match='changed'):identity.capture('jobbench',[job.row.task_id],{},{job.row.task_id:old})
    entry=deepcopy(job.entry);entry.update(judge.selection())
    job.manifest.write_text(json.dumps({'schema':'harnessgrad.native-harbor-runtime.v1','tasks':{job.row.task_id:entry}}))
    _,new,new_selected,new_context=capture(job,monkeypatch)
    assert new_context!=context and new_selected==selected and new['scorer_input']['sha256']!=old['scorer_input']['sha256']
    from harnessgrad.loops.mode_a import _identical_prior
    assert _identical_prior([point],'same',1,dataset_runtime_identity=selected,verification_context=new_context) is None


def test_direct_legacy_cached_spec_cannot_omit_judge_marker(job,monkeypatch,tmp_path):
    from eval.run import runner
    from eval.run.measurement_cache import MeasurementIdentity
    task,spec,selected,_=capture(job,monkeypatch)
    spec.pop('native_judge_identity');spec.pop('result_contract')
    context=verification_context.capture([task],{},verifiers={job.row.task_id:spec})
    key=MeasurementIdentity('same',job.row.task_id,1,context,selected,None,{},None,None,None).key()
    cache={key:{'score':1,'trace':'','verdict':{'score':1},'verification_context':context,'dataset_runtime_identity':selected}}
    before=deepcopy(cache);repo=tmp_path/'actor';repo.mkdir()
    monkeypatch.setattr(runner,'run_one',lambda *a,**k:pytest.fail('legacy spec launched actor'))
    monkeypatch.setattr(runner,'snapshot',lambda root:{})
    result=runner._evaluate(repo,[task],{},cache=cache,harness_sha='same',verifiers={job.row.task_id:spec})
    assert result['per_task']=={} and job.row.task_id in result['invalid'] and not result['cached_tasks']
    assert cache==before


def test_prepare_runtime_records_exact_selection(job,monkeypatch,tmp_path):
    from data.scaffolds.native_harbor_runtime import prepare_runtime,python_runtime
    monkeypatch.setattr(python_runtime,'resolve',lambda:job.entry['harbor_runtime'])
    monkeypatch.setattr(prepare_runtime,'inspect',lambda image:{'Id':image,'Config':{}})
    output=tmp_path/'prepared.json'
    monkeypatch.setattr(sys,'argv',['prepare_runtime','--dataset','jobbench','--data-root',str(job.source),
        '--task-id',job.row.task_id,'--actor-image','sha256:'+'a'*64,'--replay-actor-image','sha256:'+'a'*64,
        '--verifier-image','sha256:'+'b'*64,'--manifest',str(output),'--workdir','/workspace',
        '--workspace-bytes',str(64<<20),'--workspace-inodes','16384','--memory-mb','256'])
    prepare_runtime.main()
    entry=json.loads(output.read_text())['tasks'][job.row.task_id]
    assert judge.admit_selection(entry)==judge.selection()
    assert 'RAW_KEY_CANARY' not in output.read_text()


def test_gateway_or_child_never_accepts_changed_selection(job,monkeypatch,tmp_path):
    marker=tmp_path/'response.json'
    monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','changed')
    monkeypatch.setattr(verify.subprocess,'Popen',lambda *a,**k:pytest.fail('changed judge started child'))
    with pytest.raises(ValueError,match='changed'):
        verify._execute_harbor(job.entry,tmp_path/'request.json',marker,{},10)


def test_child_validates_selected_model_before_harbor_import(job,monkeypatch):
    request={'case':{'dataset':'jobbench','runtime':job.entry},'verifier_env':{'JUDGE_MODEL':'wrong'}}
    monkeypatch.setattr(harbor_entry.sys,'executable',job.entry['harbor_python'])
    with pytest.raises(ValueError,match='child model'):
        asyncio.run(harbor_entry.run(request))
    assert harbor_entry.ADMITTED is False


def test_method_projection_reuses_public_identity_and_keeps_gold(job,monkeypatch,tmp_path):
    from harnessgrad.channel import _stage_task_context
    task,spec,_,_=capture(job,monkeypatch)
    source=deepcopy(spec)
    work=tmp_path/'method';work.mkdir()
    _stage_task_context(work,[task],{}, {job.row.task_id:spec},
        {'verdicts':{job.row.task_id:{'judge_identity':job.entry['judge_identity']}}},[job.row.task_id])
    page=json.loads(next((work/'_harnessgrad/tasks').rglob('*.json')).read_text())
    encoded=json.dumps(page)
    for private in ('RAW_KEY_CANARY',job.entry['judge_base_url'],job.entry['judge_identity']['endpoint_sha256']):assert private not in encoded
    assert page['verdict']['judge_identity']==judge.public_summary(job.entry['judge_identity'])
    assert json.loads(page['verifier']['checks']['native-case.json'])['declaration']==job.row.declaration
    assert spec==source


@pytest.mark.parametrize('drift',['model','endpoint','response','none'])
def test_actual_child_boundary_rechecks_judge_after_process(job,monkeypatch,tmp_path,drift):
    response=tmp_path/'response.json';original=verify.subprocess.Popen;calls=[]
    class Process:
        returncode=0
        def communicate(self,timeout=None):
            value={'harbor_runtime':job.entry['harbor_runtime'],'judge_identity':deepcopy(job.entry['judge_identity'])}
            if drift=='model':monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','post-child-model')
            elif drift=='endpoint':monkeypatch.setenv('HG_JUDGE_HARBOR_BASE_URL','http://127.0.0.1:11/v1')
            elif drift=='response':value['judge_identity']['model']='wrong-child'
            response.write_text(json.dumps(value));return '', ''
    def popen(argv,**kwargs):
        if list(argv[1:3])==['-I','-B'] and str(argv[3]).endswith('harbor_entry.py'):
            calls.append(argv);return Process()
        return original(argv,**kwargs)
    monkeypatch.setattr(verify.subprocess,'Popen',popen)
    if drift=='none':
        result=verify._execute_harbor(job.entry,tmp_path/'request.json',response,{},10)
        assert result['judge_identity']==job.entry['judge_identity']
    else:
        with pytest.raises(ValueError,match='changed|different judge'):
            verify._execute_harbor(job.entry,tmp_path/'request.json',response,{},10)
    assert len(calls)==1


@pytest.mark.parametrize('drift',['before-gateway','during-metadata','post-child','case-marker','child-marker','none'])
def test_real_scorer_boundary_uses_prepared_identity_and_refuses_drift(job,monkeypatch,tmp_path,drift):
    from data.scaffolds.native_harbor_runtime import judge_gateway
    _,spec,_,_=capture(job,monkeypatch)
    case=json.loads(spec['inputs'][0]['content']);root=tmp_path/'trusted-artifacts';root.mkdir()
    (root/'native-case.json').write_text(spec['inputs'][0]['content'])
    episode=root/'native_episode';episode.mkdir()
    payload={'task_id':job.row.task_id,'dataset':'jobbench','case_sha256':spec['scorer_input']['sha256'],
             'source_sha256':job.row.source_sha256,'finished':True,'failure':None,
             'actor_runtime':{'cleanup':{'complete':True}},'artifacts':{}}
    (episode/'episode.json').write_text(json.dumps({'binding':{'task_id':job.row.task_id,'run_id':'owned','trial_id':'one'},'payload':payload}))
    opened=[];closed=[];children=[]
    def metadata(argv,**kwargs):
        assert argv==['docker','network','inspect','bridge']
        if drift=='during-metadata':monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','metadata-drift')
        return json.dumps([{'IPAM':{'Config':[{'Gateway':'172.17.0.1'}]}}])
    monkeypatch.setattr(verify.subprocess,'check_output',metadata)
    def gateway(base,bridge):
        opened.append((base,bridge));return SimpleNamespace(base_url='http://172.17.0.1:1234/v1',stop=lambda:closed.append(True))
    monkeypatch.setattr(judge_gateway,'open_gateway',gateway)
    def child(entry,request,response,environment,timeout):
        children.append(True);private=json.loads(request.read_text())
        assert private['verifier_env']['JUDGE_MODEL']=='judge-A'
        assert private['verifier_env']['JUDGE_API_KEY']=='RAW_KEY_CANARY'
        result={'judge_identity':deepcopy(entry['judge_identity']),'reward':0.75,'native_rewards':{'reward':0.75}}
        if drift=='post-child':monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','post-drift')
        if drift=='child-marker':result['judge_identity']['model']='wrong-child'
        return result
    monkeypatch.setattr(verify,'_execute_harbor',child)
    if drift=='before-gateway':monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','pre-drift')
    if drift=='case-marker':spec['native_judge_identity']={**spec['native_judge_identity'],'model':'forged'}
    result=verify.run(spec,root,task_id=job.row.task_id,run_id='owned',trial_id='one',timeout_s=20)
    if drift=='none':
        assert result['score']==0.75 and result['native_harbor']['judge_identity']==job.entry['judge_identity']
    else:
        assert result['score'] is None and result['outcome']=='unmeasured'
    if drift in {'before-gateway','during-metadata','case-marker'}:assert opened==[] and children==[]
    else:assert len(opened)==len(closed)==len(children)==1
    assert 'RAW_KEY_CANARY' not in json.dumps(result)


def test_actual_h0_and_context_follow_prepared_judge_not_ambient_changes(tmp_path,monkeypatch):
    from data.scaffolds.native_harbor_runtime import python_runtime
    from tools.prepare_h0 import _verification_preflight
    store=os.environ.get('HG_TEST_NATIVE_HARBOR_RUNTIME_ROOT')
    if not store:pytest.skip('explicit full Harbor artifact required for actual H0 subprocess')
    source=tmp_path/'native';task=source/'tasks/authored';task.mkdir(parents=True)
    (task/'instruction.md').write_text('Authored H0 control, no model')
    (task/'task.toml').write_text('artifacts=["/workspace/output"]\n[metadata]\nsplit="main"\n[agent]\nnetwork_mode="no-network"\n[environment]\ndocker_image="sha256:'+('a'*64)+'"\n[verifier]\nenvironment_mode="separate"\nnetwork_mode="no-network"\n')
    row=native.inventory(source,'jobbench')[0];binding=python_runtime.resolve(root=store)
    actor={'kind':'exec','backend':'observed-v1','image':'sha256:'+'a'*64,'python':'/usr/local/bin/python3',
           'workdir':'/workspace','network':'none','cpus':1,'memory_mb':256,'pids':64,
           'filesystems':{p:{'bytes':64<<20,'inodes':16384} for p in ('/workspace','/tmp','/dev','/dev/shm')}}
    binaries=tmp_path/'bin';binaries.mkdir();docker=binaries/'docker';commands=tmp_path/'metadata-only.jsonl'
    docker.write_text('#!'+sys.executable+'\nimport sys,json\nfrom pathlib import Path\nassert sys.argv[1:3]==["image","inspect"]\nwith Path('+repr(str(commands))+').open("a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nprint("[]")\n');docker.chmod(0o755)
    env={'PATH':str(binaries)+os.pathsep+os.defpath,'HOME':str(tmp_path),'HG_REVIEWED_ENVIRONMENT_ONLY':'1',
         'HG_DATA_JOBBENCH':str(source),'HG_DATASET_RUNTIME_ROOT':store,
         'HG_JUDGE_HARBOR_BASE_URL':'http://127.0.0.1:9/v1','HG_JUDGE_HARBOR_MODEL':'judge-A',
         'HG_JUDGE_HARBOR_API_KEY':'H0_KEY_CANARY','PYTHONDONTWRITEBYTECODE':'1'}
    entry={'source_sha256':row.source_sha256,'actor':actor,'harbor_runtime':binding,'harbor_python':binding['python'],
           'replay_actor_image':'sha256:'+'a'*64,'verifier_image':'sha256:'+'b'*64,**judge.selection(env)}
    manifest=source/'native-runtime.json'
    def publish():manifest.write_text(json.dumps({'schema':'harnessgrad.native-harbor-runtime.v1','tasks':{row.task_id:entry}}))
    def h0():return identity.resolve_h0('jobbench',side='eval',task_ids=[row.task_id],sampling='all',env=env)
    def context():return _verification_preflight('jobbench','eval',[row.task_id],'declared','all',env)
    publish();selected=h0();before=context();count=len(commands.read_text().splitlines())
    env['HG_JUDGE_HARBOR_MODEL']='judge-B'
    with pytest.raises(ValueError,match='H0 preflight failed'):h0()
    with pytest.raises(ValueError):context()
    assert len(commands.read_text().splitlines())==count
    entry.update(judge.selection(env));publish()
    assert h0()==selected and context()!=before
    stable=context();env['HG_JUDGE_HARBOR_API_KEY']='ROTATED_H0_KEY_CANARY'
    assert h0()==selected and context()==stable
    assert 'KEY_CANARY' not in manifest.read_text()
    entry.pop('judge_identity');publish()
    with pytest.raises(ValueError,match='H0 preflight failed'):h0()


def test_export_keeps_endpoint_identity_private_and_detects_context_change(job,monkeypatch,tmp_path):
    import importlib.util
    from harnessgrad import evidence_export
    fixtures_path=Path(__file__).with_name('test_evidence_export.py')
    loader=importlib.util.spec_from_file_location('authored_export',fixtures_path)
    fixture=importlib.util.module_from_spec(loader);loader.loader.exec_module(fixture)
    task,spec,_,old=capture(job,monkeypatch)
    entry=deepcopy(job.entry);monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','judge-B');entry.update(judge.selection())
    job.manifest.write_text(json.dumps({'schema':'harnessgrad.native-harbor-runtime.v1','tasks':{job.row.task_id:entry}}))
    _,_,_,new=capture(job,monkeypatch)
    root=tmp_path/'runs';fixture.save_run(root,'base',rows=[[1,1,1]]);fixture.save_run(root,'sub',1,rows=[[1,1,1]])
    for name,context,marker in [('base',old,job.entry['judge_identity']),('sub',new,entry['judge_identity'])]:
        def point(value):
            value.update(per_task={job.row.task_id:1},per_task_trials={job.row.task_id:[1,1,1]},
                         per_task_scorer_isolation={job.row.task_id:'separate-pristine'},
                         verification_context=context,native_judge_identity=marker)
            value['sampling']['task_ids']=[job.row.task_id]
        fixture.change(root,name,'curve.jsonl',point)
    bundle=evidence_export.build_export(fixture.author(),root)
    assert not bundle['comparisons'][0]['comparable']
    assert 'verification_context' in bundle['comparisons'][0]['changed_conditions']
    text=json.dumps(bundle)
    assert job.entry['judge_base_url'] not in text and job.entry['judge_identity']['endpoint_sha256'] not in text
    assert 'RAW_KEY_CANARY' not in text and evidence_export.check_export(bundle)==[]
