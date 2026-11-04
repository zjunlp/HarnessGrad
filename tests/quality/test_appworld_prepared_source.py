"""Prepared candidate identity wiring; synthetic sources only, no native/model run."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from tools import appworld_campaign_state as state
from tools import launch_appworld_harness_baselines as launcher


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    platform = tmp_path/'platform'; platform.mkdir()
    source = tmp_path/'prepared'; source.mkdir()
    runtime = tmp_path/'runtime'; runtime.mkdir()
    cli = runtime/'cli.js'; cli.write_text('// synthetic runtime entry')
    # A separately prepared portable-DSH-style manifest, not the platform manifest.
    manifest = {'name':'deepseek-harness','version':'1','entrypoint':'agent.py',
                'backend':'cli','env_kinds':['files','exec'],'runtime_paths':[str(runtime)],'role':'harness'}
    (source/'harness.json').write_text(json.dumps(manifest))
    (source/'agent.py').write_text('# synthetic candidate, never executed here\n')
    monkeypatch.setattr(launcher, 'ROOT', platform)
    monkeypatch.setattr(launcher, 'main_checkout', lambda *_: platform)
    bindings = {'harness_sources':{'deepseek-harness':str(source)},
                'harness_runtimes':{'deepseek-harness':{'files':[{
                    'path':str(cli),'sha256':hashlib.sha256(cli.read_bytes()).hexdigest()}], 'environment':{}}}}
    return source, bindings, platform


def identity_for(source, bindings):
    path, manifest, source_id, manifest_sha, runtime = launcher.harness_inputs('deepseek-harness', bindings)
    return {'harness':'deepseek-harness','harness_sha256':source_id['submission_sha256'],
            'manifest_sha256':manifest_sha,'runtime_sha256':state.digest(runtime),
            'pool':'train','side':'train',
            'model':{'backend':'openai','name':'synthetic','endpoint':'http://127.0.0.1:8002/v1'},
            'budget':{'output_tokens':123,'deadline_s':60,'request_timeout_s':10,
                      'native_settings':{},'trials':1,'jobs':1,'seed':7}}


def test_prepared_candidate_binds_exact_path_source_manifest_and_runtime(prepared):
    source, bindings, platform = prepared
    path, manifest, source_id, manifest_sha, runtime = launcher.harness_inputs('deepseek-harness', bindings)
    assert path == source
    assert runtime['harness_source'] == {'path':str(source),
        'submission_sha256':source_id['submission_sha256'],'manifest_sha256':manifest_sha}
    assert manifest['runtime_paths'] == [str(source.parent/'runtime')]
    assert not list(platform.iterdir()), 'preparation modified frozen platform'
    assert runtime['scope'].endswith('transitive closure not asserted')


def test_different_prepared_path_changes_runtime_identity_even_with_same_bytes(prepared):
    source, bindings, _ = prepared
    first = launcher.harness_inputs('deepseek-harness', bindings)
    second = source.parent/'other'; shutil.copytree(source, second)
    moved = copy.deepcopy(bindings); moved['harness_sources']['deepseek-harness'] = str(second)
    other = launcher.harness_inputs('deepseek-harness', moved)
    assert first[2] == other[2] and first[3] == other[3]
    assert state.digest(first[4]) != state.digest(other[4])


def test_candidate_edit_changes_submission_and_runtime_identity(prepared):
    source, bindings, _ = prepared
    original = launcher.harness_inputs('deepseek-harness', bindings)
    (source/'agent.py').write_text('# changed candidate\n')
    changed = launcher.harness_inputs('deepseek-harness', bindings)
    assert original[2] != changed[2] and state.digest(original[4]) != state.digest(changed[4])


def test_command_and_execution_reuse_exact_reviewed_prepared_candidate(prepared, monkeypatch):
    source, bindings, _ = prepared
    identity = identity_for(source, bindings)
    monkeypatch.setattr(launcher,'dataset_environment',lambda:{'HG_APPWORLD_IMAGE':'synthetic'})
    monkeypatch.setattr(launcher,'validate_appworld_overlay',lambda path,*_: None if path==source else pytest.fail('mixed candidate'))
    env=launcher.execution_environment(identity,bindings)
    assert env['HG_AGENT_MODEL']=='synthetic'
    command=launcher.command_for(identity,['a'],Path('/tmp/appworld-reviewed-source'),bindings)
    assert command[command.index('--harness-path')+1]==str(source)
    assert command[command.index('--expected-harness-sha256')+1]==identity['harness_sha256']
    (source/'agent.py').write_text('# changed after review\n')
    with pytest.raises(state.RecoveryError,match='source changed'):
        launcher.execution_environment(identity,bindings)


def test_run_driver_rehashes_prepared_source_before_subprocess(prepared, monkeypatch, tmp_path):
    source, bindings, _ = prepared
    identity = identity_for(source, bindings)
    monkeypatch.setattr(launcher,'dataset_environment',lambda:{})
    monkeypatch.setattr(launcher.subprocess,'run',lambda *a,**k:pytest.fail('modified source launched driver'))
    (source/'agent.py').write_text('# changed after command construction\n')
    with (tmp_path/'log').open('w') as log, (tmp_path/'lock').open('w') as lock:
        with pytest.raises(state.RecoveryError,match='source changed'):
            launcher.run_driver(['synthetic-driver'],identity,bindings,log,lock.fileno())


@pytest.mark.parametrize('kind', ['relative','missing','file','alias','platform','git','wrong-name','control','unsafe-link','mapping'])
def test_invalid_prepared_sources_refuse(prepared, kind):
    source, bindings, platform = prepared
    if kind=='relative': bindings['harness_sources']['deepseek-harness']='relative'
    elif kind=='missing': bindings['harness_sources']['deepseek-harness']=str(source.parent/'missing')
    elif kind=='file': bindings['harness_sources']['deepseek-harness']=str(source/'agent.py')
    elif kind=='alias':
        alias=source.parent/'alias';alias.symlink_to(source, target_is_directory=True)
        bindings['harness_sources']['deepseek-harness']=str(alias)
    elif kind=='platform': bindings['harness_sources']['deepseek-harness']=str(platform)
    elif kind=='git': (source/'.git').write_text('gitdir: /untrusted')
    elif kind=='unsafe-link': (source/'escape').symlink_to(platform, target_is_directory=True)
    elif kind=='mapping': bindings['harness_sources']=[]
    else:
        manifest=json.loads((source/'harness.json').read_text())
        manifest['name' if kind=='wrong-name' else 'role']='wrong' if kind=='wrong-name' else 'control'
        (source/'harness.json').write_text(json.dumps(manifest))
    with pytest.raises((state.RecoveryError,OSError)):
        launcher.harness_inputs('deepseek-harness',bindings)


def test_default_source_and_runtime_contract_remain_supported(prepared):
    source, bindings, platform = prepared
    target=platform/'base_harness/deepseek-harness';target.parent.mkdir();shutil.copytree(source,target)
    bindings.pop('harness_sources')
    path,_,_,_,runtime=launcher.harness_inputs('deepseek-harness',bindings)
    assert path==target and 'harness_source' not in runtime


def test_current_identity_uses_prepared_candidate_not_portable_platform_manifest(prepared,monkeypatch):
    source,bindings,platform=prepared
    bundle=source.parent/'bundle';bundle.write_bytes(b'synthetic data')
    (platform/'data/datasets/appworld/verify').mkdir(parents=True)
    dataset=SimpleNamespace(BUNDLE=bundle,split_ids=lambda _:{'train':['a']},
        IMAGE='sha256:'+'1'*64,SERVICE_IMAGE='sha256:'+'2'*64,VERIFIER_IMAGE='sha256:'+'3'*64)
    monkeypatch.setitem(sys.modules,'data.datasets.appworld',dataset)
    monkeypatch.setattr(launcher,'preflight_appworld_data',lambda:None)
    monkeypatch.setattr(launcher.subprocess,'check_output',lambda *a,**k:'a'*40+'\n')
    monkeypatch.setattr(launcher,'validate_identity',lambda identity:identity)
    expected=identity_for(source,bindings);bindings['model']=expected['model']
    actual=launcher.current_identity(expected,bindings)
    assert actual['harness_sha256']==expected['harness_sha256']
    assert actual['runtime_sha256']==expected['runtime_sha256']
    assert not (platform/'base_harness').exists()


def test_four_pool_selection_prepares_external_candidate_with_real_identity_validation(prepared, monkeypatch):
    from tools import prepare_appworld_selection as prep
    source,bindings,platform=prepared
    bundle=source.parent/'bundle';bundle.write_bytes(b'synthetic official-bundle fixture')
    pools={pool:[pool+'-'+str(i) for i in range(count)] for pool,count in
           [('train',90),('dev',57),('test_normal',168),('test_challenge',417)]}
    dataset=SimpleNamespace(BUNDLE=bundle,split_ids=lambda _:pools,
        IMAGE='sha256:'+'1'*64,SERVICE_IMAGE='sha256:'+'2'*64,VERIFIER_IMAGE='sha256:'+'3'*64)
    (platform/'data/datasets/appworld/verify').mkdir(parents=True)
    monkeypatch.setitem(sys.modules,'data.datasets.appworld',dataset)
    monkeypatch.setattr(prep,'preflight_appworld_data',lambda:None)
    monkeypatch.setattr(launcher,'preflight_appworld_data',lambda:None)
    monkeypatch.setattr(launcher.subprocess,'check_output',lambda *a,**k:'a'*40+'\n')
    bindings['model']={'backend':'openai','name':'synthetic','endpoint':'http://127.0.0.1:8002/v1',
                       'weights_sha256':'1'*64,'tokenizer_sha256':'2'*64,'deployment_sha256':'3'*64}
    budgets={'deepseek-harness':{'deadline_s':60,'request_timeout_s':10,'output_tokens':128,
        'trials':1,'seed':7,'jobs':1,'native_settings':{},'context_policy':'native-defaults'}}
    selection=prep.prepare(bindings,budgets,['deepseek-harness'])
    assert [len(selection['pools'][p]['selected']) for p in state.POOLS]==[9,6,17,30]
    assert len(selection['identities'])==4 and selection['prepared_for_review'] is True
    for identity in selection['identities'].values():
        state.validate_identity(identity)
        assert identity['harness_sha256']==launcher.source_identity(source)['submission_sha256']
        cmd=launcher.command_for(identity,['synthetic-task'],Path('/tmp/appworld-synthetic'),bindings)
        assert cmd[cmd.index('--harness-path')+1]==str(source)
        assert cmd[cmd.index('--expected-harness-sha256')+1]==identity['harness_sha256']
