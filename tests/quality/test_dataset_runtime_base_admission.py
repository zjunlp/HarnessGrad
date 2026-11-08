"""Real owned Python canaries: admission must not execute a changed base binary."""
import json
from pathlib import Path
import shutil
import sys
import sysconfig

import pytest

from harnessgrad import dataset_runtime as runtime
from harnessgrad import dataset_runtime_identity as identity


@pytest.fixture
def owned_runtime(tmp_path, monkeypatch, request):
    if shutil.which('uv') is None:
        pytest.skip('offline real artifact controls require uv')
    profile = request.param
    # Never change the actual interpreter or its system prefix. The copied ELF
    # finds stdlib through this private prefix; venv packages remain isolated.
    base = tmp_path/'owned-base'
    (base/'bin').mkdir(parents=True)
    (base/'lib').mkdir()
    executable = base/'bin/python'
    shutil.copy2(Path(sys.executable).resolve(), executable)
    (base/'lib'/('python'+sysconfig.get_python_version())).symlink_to(
        sysconfig.get_path('stdlib'), target_is_directory=True)
    zip_name = 'python'+sysconfig.get_python_version().replace('.', '')+'.zip'
    (base/'lib'/zip_name).symlink_to(Path(sysconfig.get_path('stdlib')).parent/zip_name)
    folder = tmp_path/'source/data/datasets/control'
    folder.mkdir(parents=True)
    (folder/'empty.lock').write_text('# authored offline empty runtime\n')
    declaration = {'schema': runtime.EXECUTION_SCHEMA if profile == 'execution' else runtime.SCHEMA,
                   'dataset':'control','python':sysconfig.get_python_version(),'requirements':'empty.lock'}
    if profile == 'provider':
        declaration['provider'] = 'qa'
    (folder/('execution-runtime.json' if profile == 'execution' else 'runtime.json')).write_text(json.dumps(declaration))
    monkeypatch.setattr(runtime, 'ROOT', tmp_path/'source')
    monkeypatch.setenv('UV_OFFLINE', '1')
    store = tmp_path/'store'
    prepare = runtime.prepare_execution if profile == 'execution' else runtime.prepare
    selected = prepare('control', python=str(executable), root=store)
    manifest = json.loads(Path(selected['manifest_path']).read_text())
    assert manifest['base_python']['executable'] == str(executable)
    assert Path(selected['python']).resolve() == executable
    marker = tmp_path/'BASE_CANARY_EXECUTED'
    yield profile, store, selected, executable, marker
    runtime._remove_instance(store)


def corrupt_base(executable, marker):
    executable.chmod(0o755)
    executable.write_text('#!/bin/sh\nprintf executed > '+str(marker)+'\nexit 0\n')


def readmit(profile, store, selected, route):
    if route == 'resolve':
        return (runtime.resolve_execution if profile == 'execution' else runtime.resolve)('control', root=store)
    if route == 'check':
        return (runtime.check_execution if profile == 'execution' else runtime.check)('control', root=store)
    if route == 'binding':
        return (runtime.admit_binding(runtime.bind(selected, 'verifier'), 'verifier')
                if profile == 'execution' else runtime.admit_provider('control', selected))
    if route == 'prepare-cache':
        return (runtime.prepare_execution if profile == 'execution' else runtime.prepare)('control', root=store)
    if route == 'identity':
        value = (identity.create(tasks={'task':{'verifier':runtime.bind(selected,'verifier')}})
                 if profile == 'execution' else identity.create(provider={'dataset':'control', **{key:selected[key] for key in (
                     'runtime_id','manifest_path','python','path','descriptor_sha256','requirements_sha256')}}))
        return identity.admit(value)
    raise AssertionError(route)


@pytest.mark.parametrize('owned_runtime', ['provider', 'execution'], indirect=True)
@pytest.mark.parametrize('route', ['resolve','check','binding','prepare-cache','identity'])
def test_changed_base_refused_without_executing_canary(owned_runtime, route):
    profile, store, selected, executable, marker = owned_runtime
    corrupt_base(executable, marker)
    if route == 'check':
        state = readmit(profile, store, selected, route)
        assert state['status'] == 'invalid' and not state['present']
    else:
        expected_error = ValueError if route == 'identity' else runtime.DatasetRuntimeUnavailable
        with pytest.raises(expected_error, match='base interpreter changed'):
            readmit(profile, store, selected, route)
    assert not marker.exists(), 'admission executed the changed base before checking its bytes'


@pytest.mark.parametrize('owned_runtime', ['provider', 'execution'], indirect=True)
def test_base_physical_target_change_refused_before_probe(owned_runtime, monkeypatch):
    profile, store, selected, executable, marker = owned_runtime
    moved = executable.with_name('same-bytes-another-physical-target')
    executable.rename(moved)
    executable.symlink_to(moved)
    calls = []
    original = runtime._probe
    def probe(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime, '_probe', probe)
    with pytest.raises(runtime.DatasetRuntimeUnavailable):
        readmit(profile, store, selected, 'resolve')
    assert calls == [], 'admission probed a redirected physical interpreter'


@pytest.mark.parametrize('owned_runtime', ['provider', 'execution'], indirect=True)
def test_unchanged_real_artifact_keeps_identity_and_normal_probe(owned_runtime, monkeypatch):
    profile, store, selected, executable, marker = owned_runtime
    before = Path(selected['manifest_path']).read_bytes()
    calls = []
    original = runtime._probe
    def probe(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime, '_probe', probe)
    assert readmit(profile, store, selected, 'resolve')['runtime_id'] == selected['runtime_id']
    assert readmit(profile, store, selected, 'check')['status'] == 'ready'
    assert readmit(profile, store, selected, 'binding')['runtime_id'] == selected['runtime_id']
    assert readmit(profile, store, selected, 'prepare-cache')['reused'] is True
    assert len(calls) == 4
    assert Path(selected['manifest_path']).read_bytes() == before and not marker.exists()


@pytest.mark.parametrize('owned_runtime', ['execution'], indirect=True)
def test_measurement_cache_rejects_changed_base_without_actor_or_probe(owned_runtime, tmp_path, monkeypatch):
    from eval.run import runner
    profile, store, selected, executable, marker = owned_runtime
    repo = tmp_path/'actor'
    repo.mkdir()
    calls = []
    def actor(_repo, task, **kwargs):
        calls.append(task['task_id'])
        return {'task_id':task['task_id'],'trace':'','exit_code':0,'stderr':'',
                'verdict':{'kind':'command','passed':True,'score':1,
                           'scorer_isolation_class':'actor-workdir'}}
    monkeypatch.setattr(runner, 'run_one', actor)
    monkeypatch.setattr(runner, 'snapshot', lambda root: {})
    binding = runtime.bind(selected, 'verifier')
    spec = {'kind':'command','python_runtime':binding,'runtime_paths':binding['runtime_paths'],
            'argv':[binding['python'],'-I','-B','check.py']}
    cache = {}
    def evaluate():
        return runner._evaluate(repo, [{'task_id':'task'}], {}, cache=cache,
                                harness_sha='same-source',verifiers={'task':spec})
    first = evaluate()
    assert first['per_task'] == {'task':1} and cache and calls == ['task']
    corrupt_base(executable, marker)
    second = evaluate()
    assert second['per_task'] == {} and 'task' in second['invalid']
    assert not second['cached_tasks'] and calls == ['task']
    assert not marker.exists()
