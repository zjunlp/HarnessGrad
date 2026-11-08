"""Authored Reasoning Gym lifecycle controls; no released pool or model."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from harnessgrad import dataset_runtime, dataset_runtime_identity, verification_context

ROOT = Path(__file__).resolve().parents[2]
PREPARE = ROOT / 'data/datasets/reasoning_gym/prepare.py'


def load_prepare(monkeypatch, tmp_path):
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_DATA_REASONING_GYM', str(tmp_path / 'data'))
    spec=importlib.util.spec_from_file_location('reasoning_prepare_control', PREPARE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def runtime(root):
    path=str(root/'instances/build-a')
    return {'dataset':'reasoning_gym','profile':'execution','runtime_id':'a'*64,
            'manifest_path':path+'/artifact.json','path':path+'/venv',
            'python':path+'/venv/bin/python','runtime_paths':[path+'/venv']}


def test_execution_declaration_matches_existing_pin():
    desc=dataset_runtime.execution_descriptor('reasoning_gym')
    assert desc['python']=='3.12' and desc['pins']['reasoning-gym']=='0.1.25'
    assert len(desc['pins']) >= 20


def test_generation_uses_admitted_runtime_private_environment_and_receipt(tmp_path, monkeypatch):
    module=load_prepare(monkeypatch,tmp_path)
    root=tmp_path/'data';calls=[]
    monkeypatch.setattr(module, 'POOL_SHA256', hashlib.sha256(b'{"synthetic":true}\n').hexdigest())
    artifact=runtime(tmp_path/'store')
    monkeypatch.setenv('OPENAI_API_KEY','private-model-sentinel')
    monkeypatch.setattr(dataset_runtime,'resolve_execution',lambda name: artifact)
    monkeypatch.setattr(dataset_runtime,'admit_binding',lambda binding,role: calls.append((binding,role)))
    def worker(argv,*,cwd,env,**kwargs):
        assert argv[0]==artifact['python'] and argv[1:3]==['-I','-B']
        assert 'OPENAI_API_KEY' not in env and 'HG_DATA_REASONING_GYM' in env
        root.mkdir(parents=True,exist_ok=True)
        raw=b'{"synthetic":true}\n';(root/'pool.jsonl').write_bytes(raw)
        return SimpleNamespace(returncode=0,stdout=json.dumps([hashlib.sha256(raw).hexdigest(),1]))
    monkeypatch.setattr(module.subprocess,'run',worker)
    digest,count=module.build()
    receipt=json.loads((root/'pool.runtime.json').read_text())
    assert count==1 and receipt['operation']=='generate' and receipt['pool_sha256']==digest
    assert receipt['python_runtime']['runtime_id']=='a'*64
    assert len(calls)==2
    with pytest.raises(ValueError,match='already exists'):
        module.build()


def test_pool_check_refuses_missing_and_changed_data(tmp_path, monkeypatch):
    module=load_prepare(monkeypatch,tmp_path)
    with pytest.raises(SystemExit):
        module.main([])
    assert module.main(['--check'])==1
    module.DATASET_ROOT.mkdir(parents=True)
    module.POOL.write_text('authored, not the pinned pool')
    assert module.main(['--check'])==1


def test_import_snapshot_checks_digest_and_preserves_existing_data(tmp_path, monkeypatch):
    module=load_prepare(monkeypatch,tmp_path)
    source=tmp_path/'source.jsonl';source.write_bytes(b'authored snapshot\n')
    expected=hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(module,'POOL_SHA256',expected)
    assert module.main(['--import-pool',str(source)])==0
    assert module.POOL.read_bytes()==source.read_bytes()
    receipt=json.loads((module.DATASET_ROOT/'pool.snapshot.json').read_text())
    assert receipt['operation']=='import' and receipt['source_sha256']==receipt['pool_sha256']==expected
    assert receipt['bytes']==source.stat().st_size and 'python_runtime' not in receipt
    assert module.main(['--check'])==0
    before={p.name:p.read_bytes() for p in module.DATASET_ROOT.iterdir()}
    with pytest.raises(ValueError, match='already exists'):
        module.import_pool(source)
    assert {p.name:p.read_bytes() for p in module.DATASET_ROOT.iterdir()}==before


def test_import_rejects_changed_or_linked_snapshot_without_publication(tmp_path, monkeypatch):
    module=load_prepare(monkeypatch,tmp_path)
    source=tmp_path/'source.jsonl';source.write_bytes(b'unmatched')
    with pytest.raises(ValueError,match='pinned digest'):
        module.import_pool(source)
    assert not module.DATASET_ROOT.exists()
    alias=tmp_path/'alias';alias.symlink_to(source)
    with pytest.raises(OSError):
        module.import_pool(alias)
    assert not module.DATASET_ROOT.exists()
    module.DATASET_ROOT.mkdir(parents=True)
    marker=module.DATASET_ROOT/'keep';marker.write_text('unchanged')
    with pytest.raises(ValueError,match='already exists'):
        module.import_pool(source)
    assert marker.read_text()=='unchanged' and list(module.DATASET_ROOT.iterdir())==[marker]


def test_loader_rejects_unpinned_pool_without_importing_package(tmp_path):
    root=tmp_path/'data';root.mkdir();(root/'pool.jsonl').write_text('authored not pinned\n')
    code='''import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'reasoning_gym' or fullname.startswith('reasoning_gym.'):
   raise AssertionError('loader imported external package')
sys.meta_path.insert(0,Block())
import data.datasets.reasoning_gym as adapter
try:
 adapter.load()
 raise AssertionError('unpinned pool was accepted')
except Exception as exc:
 assert 'pinned SHA-256' in str(exc), str(exc)
'''
    env=dict(os.environ,HG_DATA_REASONING_GYM=str(root),HG_REVIEWED_ENVIRONMENT_ONLY='1',
             PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,env=env,
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0, result.stdout+result.stderr


def test_loader_parses_the_same_bytes_it_checks(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_DATA_REASONING_GYM', str(tmp_path/'not-provisioned'))
    spec=importlib.util.spec_from_file_location('reasoning_loader_control', ROOT/'data/datasets/reasoning_gym.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    pool=tmp_path/'pool.jsonl'
    raw=b'{"question":"original","entry":{"answer":"private"}}\n'
    pool.write_bytes(raw)
    monkeypatch.setattr(module,'DATASET_ROOT',tmp_path)
    monkeypatch.setattr(module,'POOL_SHA256',hashlib.sha256(raw).hexdigest())
    original=Path.read_bytes
    calls=[]
    def replace_after_read(path):
        value=original(path)
        if path==pool:
            calls.append(path)
            path.write_bytes(b'{"question":"replaced"}\n')
        return value
    monkeypatch.setattr(Path,'read_bytes',replace_after_read)
    rows=module._rows()['eval']
    assert calls == [pool] and rows[0]['question']=='original'
    assert b'replaced' in original(pool)


def test_generation_digest_mismatch_never_publishes_receipt(tmp_path, monkeypatch):
    module=load_prepare(monkeypatch,tmp_path)
    root=tmp_path/'data'; artifact=runtime(tmp_path/'store')
    monkeypatch.setattr(dataset_runtime,'resolve_execution',lambda name: artifact)
    monkeypatch.setattr(dataset_runtime,'admit_binding',lambda *args: None)
    def worker(*args,**kwargs):
        root.mkdir(parents=True,exist_ok=True)
        raw=b'authored nonmatching pool\n';(root/'pool.jsonl').write_bytes(raw)
        return SimpleNamespace(returncode=0,stdout=json.dumps([hashlib.sha256(raw).hexdigest(),1]))
    monkeypatch.setattr(module.subprocess,'run',worker)
    with pytest.raises(ValueError,match='differs from the pinned digest'):
        module.build()
    assert (root/'pool.jsonl').read_bytes()==b'authored nonmatching pool\n'
    assert not (root/'pool.runtime.json').exists()


def test_runtime_identity_and_verification_source_changes_refuse_reuse(tmp_path):
    artifact=runtime(tmp_path/'a'); binding=dataset_runtime.bind(artifact,'verifier')
    changed=runtime(tmp_path/'b');changed['runtime_id']='b'*64
    other=dataset_runtime.bind(changed,'verifier')
    left=dataset_runtime_identity.create(tasks={'task':{'verifier':binding}})
    right=dataset_runtime_identity.create(tasks={'task':{'verifier':other}})
    assert dataset_runtime_identity.problems(left,right)
    source=tmp_path/'scorer.py';source.write_text('first')
    tasks=[{'task_id':'task'}]; specs={'task':{'inputs':[{'path':'scorer.py','from':str(source)}]}}
    before=verification_context.capture(tasks,{},verifiers=specs)
    source.write_text('second')
    assert verification_context.problems(before,verification_context.capture(tasks,{},verifiers=specs))


def test_wrapper_accepts_qa_precision_but_rejects_unmeasured(tmp_path, monkeypatch):
    spec=importlib.util.spec_from_file_location('reasoning_check_control', ROOT/'data/datasets/reasoning_gym/check.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    (tmp_path/'verifier').mkdir()
    evidence={'measured':True,'reward':0.6499999999999999,'scorer_input':{'verified':True}}
    def child(*args,**kwargs):
        (tmp_path/'verifier/reward.txt').write_text('0.65\n')
        (tmp_path/'verifier/evidence.json').write_text(json.dumps(evidence))
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(module.subprocess,'run',child)
    assert module.main()==0
    evidence['measured']=False
    assert module.main()!=0


def test_import_atomic_publication_preserves_concurrent_empty_destination(tmp_path, monkeypatch):
    module=load_prepare(monkeypatch,tmp_path)
    source=tmp_path/'source.jsonl';source.write_bytes(b'authored snapshot\n')
    monkeypatch.setattr(module,'POOL_SHA256',hashlib.sha256(source.read_bytes()).hexdigest())
    publish=module._publish_snapshot
    def race(stage, root):
        root.mkdir()
        publish(stage, root)
    monkeypatch.setattr(module,'_publish_snapshot',race)
    with pytest.raises(ValueError,match='already exists'):
        module.import_pool(source)
    assert module.DATASET_ROOT.is_dir() and list(module.DATASET_ROOT.iterdir()) == []
    assert not list(module.DATASET_ROOT.parent.glob('.reasoning-snapshot-*'))


def test_missing_scoring_dependency_reports_preparation_command(tmp_path, monkeypatch):
    import importlib.metadata
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_DATA_REASONING_GYM', str(tmp_path/'missing'))
    spec=importlib.util.spec_from_file_location('reasoning_dependency_control', ROOT/'data/datasets/reasoning_gym.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def absent(name):
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(importlib.metadata,'version',absent)
    with pytest.raises(ValueError,match='tools.prepare_dataset --dataset reasoning_gym --runtime execution'):
        module.score('answer', {'family':'basic_arithmetic','entry':{'answer':'answer'}})
