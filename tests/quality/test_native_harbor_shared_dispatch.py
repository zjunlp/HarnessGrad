"""Actual outer verifier and fixed dispatcher with real admitted Harbor dependencies.

Only the delegated scorer side effect is replaced; no Docker, Trial or model is
started. Private case/record checks and the outer binding admission remain real.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from harnessgrad import dataset_runtime
from data.scaffolds import runtime_extensions
from eval.verification import native_benchmark
from eval.verification.verifier import _verify


@pytest.fixture(autouse=True)
def local_judge(monkeypatch):
    monkeypatch.setenv('HG_JUDGE_HARBOR_BASE_URL','http://127.0.0.1:9/v1')
    monkeypatch.setenv('HG_JUDGE_HARBOR_MODEL','authored-judge')


@pytest.fixture(scope='module')
def harbor_binding():
    store = os.environ.get('HG_TEST_NATIVE_HARBOR_RUNTIME_ROOT')
    if not store:
        pytest.skip('explicit real 90-package native Harbor runtime required')
    artifact = dataset_runtime.resolve_execution('native_harbor', root=store)
    assert len(dataset_runtime.execution_descriptor('native_harbor')['pins']) == 90
    return dataset_runtime.bind(artifact, 'verifier')


def declaration(tmp_path, binding, dataset='deepswe'):
    task_id, run_id, trial_id = 'authored/task', 'owned-run', 'owned-trial'
    actor = tmp_path/'actor';actor.mkdir()
    trusted = tmp_path/'trusted';trusted.mkdir()
    record = trusted/'episode';record.mkdir()
    case = {'task_id':task_id,'dataset':dataset,'runtime':{
        'harbor_python':binding['python'],'harbor_runtime':binding},'authored_private':'case sentinel'}
    if dataset=='jobbench':
        from harnessgrad.native_judge_identity import selection
        case['runtime'].update(selection())
    raw = json.dumps(case,sort_keys=True,separators=(',',':'))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    envelope = {'schema':'harnessgrad.interaction-record.v1',
                'binding':{'task_id':task_id,'run_id':run_id,'trial_id':trial_id},
                'payload':{'authored':'sealed-record-control'}}
    (record/'episode.json').write_text(json.dumps(envelope))
    entry = Path(runtime_extensions.__file__).resolve().with_name('native_harbor_runtime')/'harbor_entry.py'
    spec = {'kind':'command','argv':[binding['python'],'-I','-B',str(entry)],
            'python_runtime':deepcopy(binding),'runtime_paths':list(binding['runtime_paths']),
            'inputs':[{'path':'native-case.json','content':raw},
                      {'path':'native_episode','from':str(record),'immutable':True}],
            'scorer_input':{'task_id':task_id,'path':'native-case.json','sha256':digest},
            'native_benchmark':{'dataset':dataset,'case_sha256':digest,'case_path':'native-case.json','record_path':'native_episode'},
            'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}}
    if dataset=='jobbench':spec['native_judge_identity']=case['runtime']['judge_identity']
    options = {'sandbox':True,'env':{'kind':'files'},'task_id':task_id,
               'run_id':run_id,'trial_id':trial_id,'timeout_s':20}
    return actor,spec,options,case,envelope


def delegated(monkeypatch, actor, expected_case, expected_envelope):
    calls = []
    def run(spec, destination, **options):
        calls.append(options)
        assert destination != actor and not destination.is_relative_to(actor)
        assert json.loads((destination/'native-case.json').read_text()) == expected_case
        assert json.loads((destination/'native_episode/episode.json').read_text()) == expected_envelope
        assert not (actor/'native-case.json').exists()
        assert not (actor/'native_episode').exists()
        return {'kind':'command','passed':True,'score':0.5,'outcome':'measured',
                'evidence':{'measured':True,'official_evaluation_performed':True}}
    def module(key):
        assert key == 'harbor_verify'
        return SimpleNamespace(run=run)
    monkeypatch.setattr(native_benchmark, '_native_module', module)
    return calls


@pytest.mark.parametrize('dataset', ['deepswe','jobbench','swe_bench_pro'])
def test_real_outer_verify_reaches_only_the_delegated_scorer(harbor_binding,tmp_path,monkeypatch,dataset):
    actor,spec,options,case,envelope = declaration(tmp_path,harbor_binding,dataset)
    calls = delegated(monkeypatch,actor,case,envelope)
    result = _verify(actor,spec,'',**options)
    assert result['score'] == 0.5 and result['evidence']['measured'] is True, result
    assert result['python_runtime'] == harbor_binding
    assert result['scorer_isolation_class'] == 'unknown'
    assert result['evidence']['scorer_input']['verified'] is True
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['entry','legacy','hash','task','sealed-run','sealed-trial','unsealed','service','running'])
def test_real_outer_verify_refuses_foreign_or_unsealed_routes(harbor_binding,tmp_path,monkeypatch,change):
    actor,spec,options,case,envelope = declaration(tmp_path,harbor_binding)
    calls = delegated(monkeypatch,actor,case,envelope)
    if change == 'entry': spec['argv'][-1] = str(actor/'harbor_entry.py')
    elif change == 'legacy': spec['argv'] = ['harnessgrad-native','deepswe']
    elif change == 'hash':
        spec['scorer_input']['sha256'] = spec['native_benchmark']['case_sha256'] = 'f'*64
    elif change == 'task': options['task_id'] = 'foreign/task'
    elif change in {'sealed-run','sealed-trial'}:
        envelope['binding']['run_id' if change=='sealed-run' else 'trial_id'] = 'foreign'
        (Path(spec['inputs'][1]['from'])/'episode.json').write_text(json.dumps(envelope))
    elif change == 'unsealed': spec['inputs'][1]['immutable'] = False
    elif change == 'service': options['service_specs'] = [{'name':'foreign'}]
    else: options['running'] = object()
    result = _verify(actor,spec,'',**options)
    assert result['score'] is None and result['outcome'] == 'unmeasured', result
    assert calls == []
