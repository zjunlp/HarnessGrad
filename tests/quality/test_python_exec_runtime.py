"""Placement identity/refusal controls; authored wheels and existing local images."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from eval.ckpt.source_tree import runtime_spec
from harnessgrad import candidate_runtime, python_exec_runtime as runtime
from harnessgrad import python_harness_runtime as native
from test_python_harness_store import candidate, wheel  # noqa: F401
from test_loop_managed_runtime import loop_store, loop_images  # noqa: F401


@pytest.fixture
def declared(candidate):
    path=candidate/'harness.json'
    manifest=json.loads(path.read_text())
    manifest['runtime']['exec']={'requirements':{'3.12':'requirements.lock'}}
    path.write_text(json.dumps(manifest))
    return candidate


@pytest.mark.parametrize('execution',[True,'image',{}, {'requirements':{}},
    {'requirements':{'3.12':'../escape.lock'}}, {'requirements':{'3.12':'/absolute.lock'}},
    {'requirements':{'3.12':'missing.lock'}}, {'requirements':{'bad':'requirements.lock'}},
    {'requirements':{'3.12':'requirements.lock'},'fallback':True}])
def test_exec_declaration_requires_explicit_owned_locks(candidate,execution):
    path=candidate/'harness.json'
    manifest=json.loads(path.read_text())
    manifest['runtime']['exec']=execution
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        runtime_spec(candidate)


def test_secondary_lock_content_is_part_of_the_placement_declaration(declared):
    manifest=json.loads((declared/'harness.json').read_text())
    manifest['runtime']['exec']['requirements']['3.9']='old.lock'
    (declared/'old.lock').write_text('# empty closure\n')
    (declared/'harness.json').write_text(json.dumps(manifest))
    before=runtime.descriptor(declared)
    (declared/'old.lock').write_text('# same packages, changed owned lock bytes\n')
    after=runtime.descriptor(declared)
    assert before['placements_sha256']!=after['placements_sha256']
    assert before['declaration_sha256']==after['declaration_sha256']


@pytest.mark.parametrize('environment',[
    {'kind':'exec','image':'mutable:tag'},
    {'kind':'exec','image':'sha256:'+'a'*64,'image_digest':'sha256:'+'b'*64},
    {'kind':'exec','image':'sha256:'+'a'*64,'backend':'observed-v1'},
    {'kind':'exec','image':'sha256:'+'a'*64,'agent_runtime':{}},
])
def test_bad_placements_fail_before_build_or_actor(declared,monkeypatch,environment):
    def forbidden(*args,**kwargs):
        pytest.fail('unsupported placement prepared dependencies')
    monkeypatch.setattr(native,'prepare',forbidden)
    with pytest.raises(ValueError):
        candidate_runtime.prepare(declared,envs={'t':environment})


def test_discovery_distinguishes_explicit_absence_from_a_broken_interpreter(monkeypatch):
    monkeypatch.setattr(runtime,'_run',lambda *a,**k:'')
    assert runtime.image_base('sha256:'+'a'*64) is None
    def failed(*args,**kwargs):
        raise native.RuntimeUnavailable('interpreter could not start')
    monkeypatch.setattr(runtime,'_run',failed)
    with pytest.raises(native.RuntimeUnavailable,match='could not start'):
        runtime.image_base('sha256:'+'a'*64)


def test_image_inventory_refuses_links_to_undeclared_host_files(tmp_path):
    venv=tmp_path/'venv'
    (venv/'bin').mkdir(parents=True)
    (venv/'bin/python').symlink_to('/usr/local/bin/python3.12')
    base={'executable':'/usr/local/bin/python3.12'}
    assert runtime._inventory(venv,base)['bin/python']['kind']=='link'
    (venv/'unowned').symlink_to(tmp_path/'secret')
    with pytest.raises(native.RuntimeUnavailable,match='escaping link'):
        runtime._inventory(venv,base)


def test_a_frozen_fallback_cannot_replace_an_image_interpreter(monkeypatch):
    image='sha256:'+'a'*64
    monkeypatch.setattr(runtime.container,'resolve_digest',lambda image:image)
    monkeypatch.setattr(runtime,'image_base',lambda image:{'executable':'/usr/bin/python3'})
    def forbidden(*args,**kwargs):
        pytest.fail('invalid fallback started a native interpreter')
    monkeypatch.setattr(runtime,'_run',forbidden)
    with pytest.raises(native.RuntimeUnavailable,match='Image supplies Python'):
        runtime._admit(None,{},image,{'base':None,'artifact':{}},probe=True)


def test_image_wheels_are_not_started_during_preparation_or_admission(declared,loop_images,monkeypatch,tmp_path):
    wheelhouse=tmp_path/'wheels'
    wheel(wheelhouse,hook="import pathlib; pathlib.Path.cwd().joinpath('startup-hook-ran').write_text('started')\n")
    (declared/'requirements.lock').write_text('hg-store-fixture==1.0\n')
    monkeypatch.setenv('HG_HARNESS_WHEELHOUSE',str(wheelhouse))
    image=loop_images['python']
    envs={'t':{'kind':'exec','image':image}}
    first=candidate_runtime.prepare(declared,envs=envs)['identity']
    item=runtime.placement(first,envs['t'])
    assert item['base'] is not None
    artifact=item['artifact']
    assert not Path(artifact['python']).is_symlink()
    assert native.digest(artifact['python'])==item['base']['sha256']
    assert not (Path(artifact['manifest_path']).parent/'startup-hook-ran').exists()
    assert candidate_runtime.admit(declared,first,envs=envs)==first
    assert not (Path(artifact['manifest_path']).parent/'startup-hook-ran').exists()
    # A source edit rebinds the same dependency artifacts and cannot reuse the old
    # source identity. No candidate code or installed package is imported here.
    (declared/'owned-edit.py').write_text('VALUE=2\n')
    changed=candidate_runtime.prepare(declared,envs=envs)['identity']
    assert changed['native']['artifact']==first['native']['artifact']
    assert changed['images']==first['images'] and changed['source_sha256']!=first['source_sha256']
    with pytest.raises(ValueError,match='source or declaration changed'):
        candidate_runtime.admit(declared,first,envs=envs)


def test_exact_image_binding_survives_new_publication_and_refuses_corrupt_original(declared,loop_images,monkeypatch):
    envs={'t':{'kind':'exec','image':loop_images['python']}}
    first=runtime.prepare(declared,envs=envs,build=True)['identity']
    second=runtime.prepare(declared,envs=envs,build=True,force=True)['identity']
    assert second['images']!=first['images']
    assert candidate_runtime.admit(declared,first,envs=envs)==first
    other={'t':{'kind':'exec','image':loop_images['absent']}}
    with pytest.raises(ValueError,match='absent from the original'):
        candidate_runtime.admit(declared,first,envs=other)
    extended=candidate_runtime.prepare(declared,binding=first,envs=other,extend_images=True)['identity']
    assert extended['native']==first['native']
    assert all(extended['images'][key]==value for key,value in first['images'].items())
    target=Path(runtime.placement(first,envs['t'])['artifact']['python'])
    mode=target.stat().st_mode
    target.chmod(mode|0o200)
    data=target.read_bytes()
    try:
        target.write_bytes(data+b'corrupt original binding')
        with pytest.raises(ValueError,match='inventory changed'):
            candidate_runtime.admit(declared,first,envs=envs)
        from eval.run import runner
        class NoCache(dict):
            def get(self,*args):
                pytest.fail('corrupt runtime reached the measurement cache')
        def forbidden(*args,**kwargs):
            pytest.fail('corrupt runtime launched an actor')
        monkeypatch.setattr(runner,'run_one',forbidden)
        result=runner._evaluate(declared,[{'task_id':'t','goal':'authored cache control'}],{},
            cache=NoCache({'old-score':{'score':1}}),harness_sha='authored',envs=envs,
            harness_runtime_identity=first,persist_trial_report=False)
        assert not result['per_task'] and not result['cached_tasks']
        assert result['invalid']['t']['stage']=='harness runtime preparation'
        assert candidate_runtime.admit(declared,second,envs=envs)==second
    finally:
        target.write_bytes(data)
        target.chmod(mode)


def test_check_only_does_not_create_a_store_for_an_unprepared_image(declared,loop_images,tmp_path):
    command=[sys.executable,'-B',str(Path(runtime.__file__).resolve().parents[1]/'tools/configure_harness.py'),
        '--harness',str(declared),'--image',loop_images['python'],'--runtime-root',str(tmp_path/'unused'),
        '--list','--json']
    proc=subprocess.run(command,capture_output=True,text=True,timeout=120)
    assert proc.returncode==1,proc.stdout+proc.stderr
    assert json.loads(proc.stdout)['status']=='invalid'
    assert not (tmp_path/'unused').exists()
