"""Canonical pre-cache admission for built-in native Harbor verifier declarations."""
from copy import deepcopy
from pathlib import Path

import pytest

from harnessgrad import dataset_runtime as runtime
from harnessgrad import dataset_runtime_identity as identity
from harnessgrad import verification_context

DATASETS = ('deepswe', 'jobbench', 'swe_bench_pro')


def binding():
    venv = '/authored/native-harbor/instance/venv'
    return {'schema':runtime.BINDING_SCHEMA,'dataset':'native_harbor','profile':'execution',
            'role':'verifier','runtime_id':'a'*64,'manifest_path':str(Path(venv).parent/'artifact.json'),
            'path':venv,'python':venv+'/bin/python','runtime_paths':[venv,'/authored/base']}


@pytest.fixture(autouse=True)
def local_judge(monkeypatch):
    monkeypatch.setenv('HG_JUDGE_HARBOR_BASE_URL','http://127.0.0.1:9/v1')
    monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','authored-judge')


def spec(dataset, *, bound=True):
    value = {'kind':'command','argv':['harnessgrad-native',dataset],
             'native_benchmark':{'dataset':dataset,'case_sha256':'b'*64,
                                 'case_path':'native-case.json','record_path':'native_episode'}}
    if dataset=='jobbench':
        from harnessgrad.native_judge_identity import selection
        value['native_judge_identity']=selection()['judge_identity']
    if bound:
        selected = binding()
        value.update(python_runtime=selected,runtime_paths=list(selected['runtime_paths']),
                     argv=[selected['python'],'-I','-B','/trusted/harbor_entry.py'])
    return value


def legacy_cache_case(tmp_path, monkeypatch, dataset):
    from eval.run import runner
    from eval.run.measurement_cache import MeasurementIdentity
    repo = tmp_path/'actor'
    repo.mkdir()
    tasks = [{'task_id':'t','goal':'authored cache admission control'}]
    verifier = spec(dataset, bound=False)
    verifiers = {'t':verifier}
    context = verification_context.capture(tasks, {}, None, verifiers, None, sandbox=True)
    key = MeasurementIdentity('source','t',1,context,None,None,{},None,None,None).key()
    cache = {key:{'score':1,'trace':'','verdict':{'kind':'command','score':1,
              'scorer_isolation_class':'actor-workdir'},'verification_context':context}}
    saved = deepcopy(cache)
    calls = []
    def actor(*args, **kwargs):
        calls.append('actor')
        raise AssertionError('legacy declaration must not reach an actor')
    monkeypatch.setattr(runner, 'run_one', actor)
    monkeypatch.setattr(runner, 'snapshot', lambda root: {})
    result = runner._evaluate(repo,tasks,{},cache=cache,harness_sha='source',verifiers=verifiers)
    return result, calls, cache, saved


@pytest.mark.parametrize('dataset', DATASETS)
def test_legacy_cached_direct_declaration_is_refused_before_reuse(tmp_path, monkeypatch, dataset):
    result, calls, cache, saved = legacy_cache_case(tmp_path, monkeypatch, dataset)
    assert result['per_task'] == {} and 't' in result['invalid'], result
    assert result['cached_tasks'] == [] and calls == []
    assert cache == saved, 'refusal must preserve the old evidence rather than rewrite it'


@pytest.mark.parametrize('dataset', DATASETS)
@pytest.mark.parametrize('entry', ['for_evaluation','capture','h0'])
def test_missing_binding_fails_at_the_shared_gate(dataset, entry, monkeypatch):
    from data.scaffolds import registry
    monkeypatch.setattr(registry, 'provider_runtime', lambda name: None)
    monkeypatch.setattr(registry, 'provider', lambda name: None)
    verifier = {'t':spec(dataset,bound=False)}
    monkeypatch.setattr(identity, 'h0_selection', lambda request: (['t'],{},verifier,{}))
    with pytest.raises(ValueError, match='native_harbor.*verifier') as refused:
        if entry == 'for_evaluation':
            identity.for_evaluation(None,[{'task_id':'t'}],{},verifier)
        elif entry == 'capture':
            identity.capture(dataset,['t'],{},verifier)
        else:
            identity._h0_preflight({'dataset':dataset})
    assert 'prepare_python prepare' in str(refused.value) and 'reprepare' in str(refused.value)


@pytest.mark.parametrize('change', ['dataset','role','profile','argv','interpreter','paths'])
def test_wrong_profile_role_or_startup_cannot_impersonate_native_harbor(change, monkeypatch):
    value = spec('deepswe')
    if change == 'dataset':
        value['python_runtime']['dataset'] = 'another_dataset'
    elif change == 'role':
        value['python_runtime']['role'] = 'service'
    elif change == 'profile':
        value['python_runtime']['profile'] = 'provider'
    elif change == 'argv':
        value['argv'].remove('-I')
    elif change == 'interpreter':
        value['argv'][0] = '/another/python'
    else:
        value['runtime_paths'] = ['/wrong']
    calls = []
    monkeypatch.setattr(runtime,'admit_binding', lambda *args,**kwargs: calls.append(args))
    with pytest.raises(ValueError):
        identity.for_evaluation(None,[{'task_id':'t'}],{},{'t':value})
    assert calls == [], 'malformed declarations must fail before artifact probing'


@pytest.mark.parametrize('dataset', DATASETS)
def test_exact_native_harbor_binding_uses_canonical_live_admission(dataset, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime,'admit_binding', lambda selected,role: calls.append((selected,role)))
    selected = spec(dataset)
    value = identity.for_evaluation(None,[{'task_id':'t'}],{},{'t':selected})
    assert value == identity.create(tasks={'t':{'verifier':selected['python_runtime']}})
    assert calls == [(selected['python_runtime'],'verifier')]
    assert identity.for_evaluation(value,[{'task_id':'t'}],{},{'t':selected}) == value
    assert len(calls) == 2


def test_unrelated_native_protocol_does_not_acquire_a_harbor_dependency():
    assert identity.for_evaluation(None,[{'task_id':'t'}],{},{'t':spec('swe_bench',bound=False)}) is None
