"""The managed Harbor entry is fixed platform dispatch, never an arbitrary command."""
from copy import deepcopy
from pathlib import Path

import pytest

from data.scaffolds import registry, runtime_extensions
from harnessgrad import dataset_runtime as runtime
from harnessgrad import dataset_runtime_identity as identity

DATASETS = ('deepswe','jobbench','swe_bench_pro')


@pytest.fixture(autouse=True)
def local_judge(monkeypatch):
    monkeypatch.setenv('HG_JUDGE_HARBOR_BASE_URL','http://127.0.0.1:9/v1')
    monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','authored-judge')


def bound_spec(dataset):
    venv = '/authored/native-harbor/instance/venv'
    binding = {'schema':runtime.BINDING_SCHEMA,'dataset':'native_harbor','profile':'execution',
               'role':'verifier','runtime_id':'a'*64,'manifest_path':str(Path(venv).parent/'artifact.json'),
               'path':venv,'python':venv+'/bin/python','runtime_paths':[venv,'/authored/base']}
    entry = Path(runtime_extensions.__file__).resolve().with_name('native_harbor_runtime')/'harbor_entry.py'
    from harnessgrad.native_judge_identity import selection
    return {**({'native_judge_identity':selection()['judge_identity']} if dataset=='jobbench' else {}),
            'kind':'command','argv':[binding['python'],'-I','-B',str(entry)],
            'python_runtime':binding,'runtime_paths':list(binding['runtime_paths']),
            'native_benchmark':{'dataset':dataset,'case_sha256':'b'*64,
                                'case_path':'native-case.json','record_path':'native_episode'},
            'scorer_input':{'task_id':'t','path':'native-case.json','sha256':'b'*64},
            'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}}


@pytest.mark.parametrize('dataset', DATASETS)
def test_bound_native_entry_passes_the_real_registry_gate(dataset, monkeypatch):
    value = bound_spec(dataset)
    before = deepcopy(value)
    registry._check_verifier(value,'t',dataset)
    assert value == before
    calls = []
    monkeypatch.setattr(runtime,'admit_binding',lambda binding,role: calls.append((binding,role)))
    selected = identity.for_evaluation(None,[{'task_id':'t'}],{},{'t':value})
    assert selected == identity.create(tasks={'t':{'verifier':value['python_runtime']}})
    assert calls == [(value['python_runtime'],'verifier')]


@pytest.mark.parametrize('change', ['untrusted-entry','relative-entry','extra-arg','python','isolation',
                                    'dataset','role','profile','paths','legacy','missing'])
def test_harbor_dispatch_cannot_impersonate_an_entry_or_runtime(change):
    value = bound_spec('deepswe')
    if change == 'untrusted-entry': value['argv'][-1] = '/actor/harbor_entry.py'
    elif change == 'relative-entry': value['argv'][-1] = 'harbor_entry.py'
    elif change == 'extra-arg': value['argv'].append('--arbitrary-command')
    elif change == 'python': value['argv'][0] = '/foreign/python'
    elif change == 'isolation': value['argv'][1] = '-c'
    elif change == 'dataset': value['python_runtime']['dataset'] = 'another_dataset'
    elif change == 'role': value['python_runtime']['role'] = 'service'
    elif change == 'profile': value['python_runtime']['profile'] = 'provider'
    elif change == 'paths': value['runtime_paths'] = ['/foreign/runtime']
    elif change == 'legacy': value['argv'] = ['harnessgrad-native','deepswe']
    else: value.pop('python_runtime')
    with pytest.raises(ValueError):
        runtime_extensions.validate_native_benchmark(value)


@pytest.mark.parametrize('field', ['judge','native_runtime','container_runtime','scorer_bundle'])
def test_conflicting_execution_fields_remain_forbidden(field):
    value = bound_spec('jobbench')
    value[field] = {}
    with pytest.raises(ValueError):
        runtime_extensions.validate_native_benchmark(value)


@pytest.mark.parametrize('dataset', ['swe_bench','harvey_lab'])
def test_other_native_routes_keep_the_original_fixed_dispatch(dataset):
    value = bound_spec(dataset)
    value.pop('python_runtime')
    value.pop('runtime_paths')
    value['argv'] = ['harnessgrad-native',dataset]
    value['native_benchmark']['record_path'] = 'swe_episode' if dataset=='swe_bench' else None
    runtime_extensions.validate_native_benchmark(value)
    value['runtime_paths'] = ['/foreign/runtime']
    with pytest.raises(ValueError):
        runtime_extensions.validate_native_benchmark(value)
    value.pop('runtime_paths')
    value['argv'] = ['/foreign/python','-I','-B','/foreign/check.py']
    with pytest.raises(ValueError):
        runtime_extensions.validate_native_benchmark(value)
