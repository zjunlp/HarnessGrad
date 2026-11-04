"""No-model checks for preparing a frozen AppWorld identity selection."""
import copy
import json
import sys
from types import ModuleType

import pytest

from tools import appworld_campaign_state as state
from tools import prepare_appworld_selection as prep
from tools import run_appworld_harness_campaign as campaign


@pytest.fixture
def inputs(monkeypatch):
    dataset = ModuleType('data.datasets.appworld')
    dataset.BUNDLE = '/synthetic/official-bundle'
    dataset.split_ids = lambda _: {
        'train': [f'train-{i}' for i in range(100)],
        'dev': [f'dev-{i}' for i in range(70)],
        'test_normal': [f'normal-{i}' for i in range(180)],
        'test_challenge': [f'challenge-{i}' for i in range(400)],
    }
    monkeypatch.setitem(sys.modules, 'data.datasets.appworld', dataset)
    monkeypatch.setattr(prep, 'preflight_appworld_data', lambda: None)
    monkeypatch.setattr(campaign, 'preflight_appworld_data', lambda: None)
    model = {'backend':'openai', 'name':'synthetic', 'endpoint':'http://127.0.0.1:8002/v1',
             'weights_sha256':'1'*64, 'tokenizer_sha256':'2'*64, 'deployment_sha256':'3'*64}
    bindings = {'model':model, 'harness_runtimes':{'miniagent':{'files':[], 'environment':{}}}}
    budgets = {'miniagent': {'deadline_s':900, 'request_timeout_s':120, 'output_tokens':2048,
                             'trials':1, 'seed':42, 'jobs':1, 'native_settings':{},
                             'context_policy':'native-defaults'}}
    calls = []
    def current(seed, binding):
        calls.append((seed['harness'], seed['pool']))
        return {**copy.deepcopy(seed), 'source_sha256':'a'*64, 'pool_ids_sha256':'b'*64,
                'scorer_sha256':'c'*64, 'platform_commit':'d'*40, 'harness_sha256':'e'*64,
                'manifest_sha256':'f'*64, 'runtime_sha256':'0'*64}
    monkeypatch.setattr(prep, 'current_identity', current)
    return bindings, budgets, calls


def test_selection_uses_explicit_budget_and_current_frozen_pilot_contract(inputs):
    bindings, budgets, calls = inputs
    result = prep.prepare(bindings, budgets, ['miniagent'])
    assert result['schema'] == state.SELECTION_SCHEMA
    assert [len(result['pools'][pool]['selected']) for pool in state.POOLS] == [9,6,17,30]
    assert [result['pools'][pool]['eligible'] for pool in state.POOLS] == [100,70,180,400]
    assert calls == [('miniagent', pool) for pool in state.POOLS]
    assert all(item['model'] == bindings['model'] and item['budget'] == budgets['miniagent']
               for item in result['identities'].values())
    assert result['prepared_for_review'] is True
    campaign.validate_selection(result, ['miniagent'], list(state.POOLS))


def test_incomplete_or_invalid_budget_never_becomes_an_identity(inputs):
    bindings, budgets, _ = inputs
    budgets['miniagent']['jobs'] = 0
    with pytest.raises(state.RecoveryError, match='invalid budget'):
        prep.prepare(bindings, budgets, ['miniagent'])
    budgets['miniagent']['jobs'] = 1
    del budgets['miniagent']['request_timeout_s']
    with pytest.raises(state.RecoveryError, match='incomplete execution budget'):
        prep.prepare(bindings, budgets, ['miniagent'])


def test_missing_runtime_and_duplicate_harness_are_rejected(inputs):
    bindings, budgets, _ = inputs
    with pytest.raises(state.RecoveryError, match='unique'):
        prep.prepare(bindings, budgets, ['miniagent', 'miniagent'])
    bindings['harness_runtimes'] = {}
    with pytest.raises(state.RecoveryError, match='runtime inventory'):
        prep.prepare(bindings, budgets, ['miniagent'])


def test_changing_data_while_preparing_blocks_selection(inputs, monkeypatch):
    bindings, budgets, calls = inputs
    original = prep.current_identity
    def drift(seed, binding):
        result = original(seed, binding)
        if len(calls) > 1:
            result['source_sha256'] = '9'*64
        return result
    monkeypatch.setattr(prep, 'current_identity', drift)
    with pytest.raises(state.RecoveryError, match='changed while'):
        prep.prepare(bindings, budgets, ['miniagent'])


def test_cli_creates_one_fresh_file_and_never_overwrites(tmp_path, inputs, monkeypatch, capsys):
    bindings, budgets, _ = inputs
    bp, up, out = (tmp_path / name for name in ('bindings.json', 'budgets.json', 'selection.json'))
    bp.write_text(json.dumps(bindings)); up.write_text(json.dumps(budgets))
    monkeypatch.setattr(prep, 'ensure_frozen', lambda: None)
    args = ['--bindings', str(bp), '--budgets', str(up), '--harness', 'miniagent', '--output', str(out)]
    assert prep.main(args) == 0
    original = out.read_bytes()
    result = json.loads(capsys.readouterr().out)
    assert result['execution_authorized'] is False and result['pairs'] == 4
    assert result['selection_sha256'] == state.digest(json.loads(original))
    with pytest.raises(SystemExit):
        prep.main(args)
    assert out.read_bytes() == original


def test_dirty_platform_refuses_before_any_output(tmp_path, inputs, monkeypatch):
    bindings, budgets, _ = inputs
    bp, up, out = (tmp_path / name for name in ('bindings.json', 'budgets.json', 'selection.json'))
    bp.write_text(json.dumps(bindings)); up.write_text(json.dumps(budgets))
    def dirty():
        raise state.RecoveryError('execution requires a clean frozen worktree')
    monkeypatch.setattr(prep, 'ensure_frozen', dirty)
    with pytest.raises(SystemExit):
        prep.main(['--bindings', str(bp), '--budgets', str(up), '--harness', 'miniagent', '--output', str(out)])
    assert not out.exists()


def test_actual_image_abi_and_requirements_provenance_are_required(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from eval.containers import container
    from tools import launch_appworld_harness_baselines as launcher
    harness = tmp_path / 'harness';harness.mkdir()
    requirements = b'example-package==1.0\n'
    (harness/'requirements.txt').write_bytes(requirements)
    (harness/'harness.json').write_text(json.dumps({'install':'requirements.txt'}))
    sha = hashlib.sha256(requirements).hexdigest()
    overlays = tmp_path/'overlays'
    good = overlays/sha[:16]/'3.11'/'PROVENANCE.json';good.parent.mkdir(parents=True)
    good.write_text(json.dumps({'install_sha256':sha,'python':'3.11'}))
    wrong = overlays/sha[:16]/'3.12'/'PROVENANCE.json';wrong.parent.mkdir(parents=True)
    wrong.write_text(json.dumps({'install_sha256':sha,'python':'3.12'}))
    calls=[]
    def local_probe(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'python':'/usr/local/bin/python3','version':'3.11'}))
    monkeypatch.setattr(container,'docker',local_probe)
    monkeypatch.setattr(launcher,'_IMAGE_ABI_CACHE',{})
    manifest={'install':'requirements.txt'}
    runtime={'environment':{'HG_OVERLAY_ROOT':str(overlays)},
             'files':[{'resolved_path':str(wrong.resolve())}]}
    with pytest.raises(state.RecoveryError,match='actual AppWorld 3.11 overlay'):
        launcher.validate_appworld_overlay(harness,manifest,runtime,'sha256:'+'a'*64)
    runtime['files']=[{'resolved_path':str(good.resolve())}]
    launcher.validate_appworld_overlay(harness,manifest,runtime,'sha256:'+'a'*64)
    assert len(calls)==1 and '--pull=never' in calls[0]
    assert calls[0][calls[0].index('--network')+1]=='none'
    good.write_text(json.dumps({'install_sha256':'0'*64,'python':'3.11'}))
    with pytest.raises(state.RecoveryError,match='requirements and Python ABI'):
        launcher.validate_appworld_overlay(harness,manifest,runtime,'sha256:'+'a'*64)
    runtime['environment']={}
    with pytest.raises(state.RecoveryError,match='absolute HG_OVERLAY_ROOT'):
        launcher.validate_appworld_overlay(harness,manifest,runtime,'sha256:'+'a'*64)


def test_missing_local_actor_image_is_refused_without_pull(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from eval.containers import container
    from tools import launch_appworld_harness_baselines as launcher
    harness=tmp_path/'harness';harness.mkdir()
    (harness/'requirements.txt').write_text('example==1\n')
    (harness/'harness.json').write_text(json.dumps({'install':'requirements.txt'}))
    calls=[]
    def missing(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout='')
    monkeypatch.setattr(container,'docker',missing)
    monkeypatch.setattr(launcher,'_IMAGE_ABI_CACHE',{})
    with pytest.raises(state.RecoveryError,match='unavailable locally'):
        launcher.validate_appworld_overlay(harness,{'install':'requirements.txt'},
            {'environment':{'HG_OVERLAY_ROOT':str(tmp_path)},'files':[]},'sha256:'+'b'*64)
    assert len(calls)==1 and '--pull=never' in calls[0]


def test_overlay_is_checked_during_launch_environment_creation(tmp_path, inputs, monkeypatch):
    from tools import launch_appworld_harness_baselines as launcher
    bindings,budgets,_=inputs
    identity=prep.current_identity({'schema':state.IDENTITY_SCHEMA,'dataset':'appworld',
        'harness':'miniagent','pool':'train','side':'train','model':bindings['model'],
        'budget':budgets['miniagent'],'policy':state.POLICY},bindings)
    runtime={'environment':{},'files':[]};identity['runtime_sha256']=state.digest(runtime)
    monkeypatch.setattr(launcher,'dataset_environment',lambda:{'HG_APPWORLD_IMAGE':'sha256:'+'a'*64})
    monkeypatch.setattr(launcher,'runtime_binding',lambda *args:runtime)
    def refuse(*args):
        raise state.RecoveryError('synthetic overlay mismatch')
    monkeypatch.setattr(launcher,'validate_appworld_overlay',refuse)
    with pytest.raises(state.RecoveryError,match='overlay mismatch'):
        launcher.execution_environment(identity,bindings)


def test_missing_appworld_inputs_are_refused_without_preparing_data(tmp_path, monkeypatch):
    from tools import launch_appworld_harness_baselines as launcher
    data = tmp_path/'missing-appworld';data.mkdir()
    monkeypatch.setenv('HG_DATA_APPWORLD', str(data))
    with pytest.raises(state.RecoveryError, match='input files are missing'):
        launcher.preflight_appworld_data()
    assert list(data.iterdir()) == []


def test_mutable_actor_image_is_refused_before_any_probe(tmp_path, monkeypatch):
    from eval.containers import container
    from tools import launch_appworld_harness_baselines as launcher
    harness=tmp_path/'harness';harness.mkdir()
    (harness/'requirements.txt').write_text('example==1\n')
    (harness/'harness.json').write_text(json.dumps({'install':'requirements.txt'}))
    monkeypatch.setattr(container,'docker',lambda *a,**k: pytest.fail('mutable image must not be probed'))
    with pytest.raises(state.RecoveryError,match='immutable digest'):
        launcher.validate_appworld_overlay(harness,{'install':'requirements.txt'},
            {'environment':{'HG_OVERLAY_ROOT':str(tmp_path)},'files':[]},'example:latest')
