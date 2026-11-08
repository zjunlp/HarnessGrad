"""Authored verification context and cache controls; no real scorers or models."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.run import runner
from harnessgrad import verification_context as context


def test_context_tracks_declarations_sources_and_private_inputs(tmp_path):
    file = tmp_path / 'scorer.py'; file.write_text('VERSION=1\n')
    directory = tmp_path / 'library'; directory.mkdir(); (directory / 'module.py').write_text('one')
    tasks = [{'task_id':'synthetic/0','goal':'PUBLIC', 'program':'ordinary task text',
              'from':'ordinary provenance'}]
    gold = {'synthetic/0':'PRIVATE-ANSWER'}
    setup = {'synthetic/0':{'files':[{'path':'library','from':str(directory)}]}}
    verifier = {'synthetic/0':{'kind':'command','argv':['python3','check.py','a'],
        'inputs':[{'path':'score.py','from':str(file)},{'path':'gold','content':'PRIVATE-GOLD'}]}}
    a = context.capture(tasks,gold,setup,verifier,{})
    assert context.validate(a) == a and 'PRIVATE' not in json.dumps(a) and str(tmp_path) not in json.dumps(a)
    changed = copy.deepcopy(verifier);changed['synthetic/0']['argv'][-1]='b'
    assert context.capture(tasks,gold,setup,changed,{}) != a
    assert context.capture(tasks,{'synthetic/0':'other'},setup,verifier,{}) != a
    file.write_text('VERSION=2\n')
    b = context.capture(tasks,gold,setup,verifier,{})
    assert b != a
    (directory/'module.py').write_text('two')
    assert context.capture(tasks,gold,setup,verifier,{}) != b
    assert context.project(a,['synthetic/0']) == a
    with pytest.raises(ValueError): context.project(a,['missing'])


def test_safe_symlink_and_escaping_alias(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'a').write_text('one')
    (source/'alias').symlink_to('a')
    task=[{'task_id':'x'}];verifiers={'x':{'inputs':[{'path':'p','from':str(source)}]}}
    first=context.capture(task,{},verifiers=verifiers)
    (source/'a').write_text('two')
    assert context.capture(task,{},verifiers=verifiers) != first
    (source/'alias').unlink(); (source/'alias').symlink_to(tmp_path/'outside')
    (tmp_path/'outside').write_text('private')
    with pytest.raises(ValueError,match='verification context unavailable'):
        context.capture(task,{},verifiers=verifiers)


def test_generated_workdir_recipe_is_not_opened(tmp_path):
    value=context.capture([{'task_id':'x'}],{},verifiers={'x':{'inputs':[
        {'path':'generated','from_workdir':'/not-created-yet'}]}})
    assert context.validate(value)==value


def test_legacy_staging_context_stays_readable_but_cannot_match_current_measurement():
    task = {'task_id': 'authored-staging'}
    # The earlier capture contract had no measurement-protocol field.
    legacy_task = {'task': task, 'scorable_present': False, 'scorable': None,
                   'setup': None, 'verifier': None, 'env': None}
    historical = context.create({task['task_id']: context._digest(legacy_task)})
    current = context.capture([task], {})
    assert context.validate(historical) == historical
    assert context.problems(historical, current) == ['verification_context changed']
    assert context.project(historical, [task['task_id']]) == historical


def test_relative_from_uses_materializer_platform_root_even_with_extra_flag(tmp_path, monkeypatch):
    from eval.observe.tracing import _materialize
    platform=tmp_path/'platform';platform.mkdir(); (platform/'source.py').write_text('first')
    work=tmp_path/'work';work.mkdir()
    monkeypatch.setattr(context, 'ROOT', platform)
    entry={'path':'check.py', 'from':'source.py', 'from_workdir': True}
    first=context.capture([{'task_id':'x'}],{},verifiers={'x':{'inputs':[entry]}})
    _materialize(work,[entry],platform)
    assert (work/'check.py').read_text() == 'first'
    (platform/'source.py').write_text('second')
    assert context.capture([{'task_id':'x'}],{},verifiers={'x':{'inputs':[entry]}}) != first


def test_bound_runtime_paths_are_references_not_source_scans(tmp_path):
    prefix='/synthetic/runtime/instances/build-a'
    base=tmp_path/'base';data=base/'share/private-data';data.parent.mkdir(parents=True);data.write_text('first')
    binding={'schema':'harnessgrad.python-runtime-binding.v1','dataset':'control',
             'profile':'execution','role':'service','runtime_id':'a'*64,
             'manifest_path':prefix+'/artifact.json','path':prefix+'/venv',
             'python':prefix+'/venv/bin/python','runtime_paths':[prefix+'/venv',str(base),'/usr']}
    program=tmp_path/'service.py'; program.write_text('pass')
    setup={'x':{'tool_relay':{'program':str(program),'python':binding['python'],
            'python_runtime':binding,'read_paths':[prefix+'/venv/site-packages',str(data)]}}}
    first=context.capture([{'task_id':'x'}],{},setups=setup)
    data.write_text('second')
    assert context.capture([{'task_id':'x'}],{},setups=setup) != first
    setup['x']['tool_relay']['read_paths']=['/usr/lib']
    with pytest.raises(ValueError, match='verification context unavailable'):
        context.capture([{'task_id':'x'}],{},setups=setup)


def test_relay_code_and_read_paths_changes_are_bound(tmp_path):
    program=tmp_path/'relay.py';program.write_text('first')
    resources=tmp_path/'resources';resources.mkdir();(resources/'case').write_text('private')
    setup={'x':{'tool_relay':{'program':str(program),'read_paths':[str(resources)]}}}
    first=context.capture([{'task_id':'x'}],{},setups=setup)
    program.write_text('second')
    second=context.capture([{'task_id':'x'}],{},setups=setup)
    assert first != second
    (resources/'case').write_text('new private')
    assert context.capture([{'task_id':'x'}],{},setups=setup) != second
    assert context.problems(None,None)


def test_cache_reuses_only_unchanged_context_and_records_progress(tmp_path):
    repo=tmp_path/'repo';repo.mkdir(); source=tmp_path/'scorer.py';source.write_text('one')
    task={'task_id':'authored'};calls=[];cache={}
    verifier={'authored':{'kind':'command','argv':['python3','check.py','v1'],
                         'inputs':[{'path':'scorer.py','from':str(source)}]}}
    def measure(_repo, task, **kwargs):
        calls.append(task['task_id'])
        return {'task_id':task['task_id'],'trace':'','exit_code':0,'stderr':'',
                'verdict':{'kind':'command','passed':True,'score':1,'scorer_isolation_class':'actor-workdir'}}
    def evaluate():
        return runner._evaluate(repo,[task],{},cache=cache,harness_sha='same',verifiers=verifier,
                                progress_dir=tmp_path/'run')
    with patch.object(runner,'run_one',measure),patch.object(runner,'snapshot',lambda root:{}):
        first=evaluate(); unchanged=evaluate()
        assert first['per_task']=={'authored':1} and unchanged['cached_tasks']==['authored'] and len(calls)==1
        progress=json.loads((tmp_path/'run/progress/round-0/authored.json').read_text())
        assert progress['verification_context']==context.project(first['verification_context'],['authored'])
        verifier['authored']['argv'][-1]='v2'
        assert not evaluate()['cached_tasks'] and len(calls)==2
        source.write_text('two')
        assert not evaluate()['cached_tasks'] and len(calls)==3
        next(iter(cache.values())).pop('verification_context')
        verifier['authored']['argv'][-1]='v1';source.write_text('one')
        assert not evaluate()['cached_tasks'] and len(calls)==4


def test_missing_and_midrun_changed_source_are_unmeasured(tmp_path):
    repo=tmp_path/'repo'; repo.mkdir(); source=tmp_path/'check.py'
    task={'task_id':'x'}; cache={}; calls=[]
    verifier={'x':{'kind':'command','argv':['python3','check.py'],
                   'inputs':[{'path':'check.py','from':str(source)}]}}
    def run(_repo, task, **kwargs):
        calls.append(task['task_id']); source.write_text('changed during run')
        return {'task_id':task['task_id'],'trace':'','exit_code':0,'stderr':'',
                'verdict':{'kind':'command','passed':True,'score':1,'scorer_isolation_class':'actor-workdir'}}
    def evaluate():
        return runner._evaluate(repo,[task],{},cache=cache,harness_sha='same',verifiers=verifier)
    with patch.object(runner,'run_one',run),patch.object(runner,'snapshot',lambda root:{}):
        missing=evaluate()
        assert missing['invalid']['x']['stage']=='verification context' and not calls
        source.write_text('before')
        changed=evaluate()
        assert changed['invalid']['x']['stage']=='verification context' and calls==['x']
        assert not changed['per_task'] and not cache


def test_execution_policy_and_native_plan_changes_cannot_reuse_task_scores(tmp_path, monkeypatch):
    from eval.containers import sandbox as sandbox_module
    repo = tmp_path / 'repo'
    repo.mkdir()
    task, cache, calls = {'task_id': 'policy-control'}, {}, []

    def measure(_repo, selected, **kwargs):
        calls.append(kwargs['sandbox'])
        return {'task_id': selected['task_id'], 'trace': '', 'exit_code': 0, 'stderr': '',
                'verdict': {'kind': 'answer', 'passed': True, 'score': 1}}

    def evaluate(sandbox):
        return runner._evaluate(repo, [task], {}, cache=cache, harness_sha='same', sandbox=sandbox)

    monkeypatch.setattr(runner, 'run_one', measure)
    monkeypatch.setattr(runner, 'snapshot', lambda root: {})
    unisolated = evaluate(False)
    assert evaluate(False)['cached_tasks'] == [task['task_id']]
    isolated = evaluate(True)
    assert not isolated['cached_tasks'] and calls == [False, True]
    assert context.problems(unisolated['verification_context'], isolated['verification_context'])
    monkeypatch.setattr(sandbox_module, 'PLAN_VERSION', sandbox_module.PLAN_VERSION + 1)
    upgraded = evaluate(True)
    assert not upgraded['cached_tasks'] and calls == [False, True, True]
    assert context.problems(isolated['verification_context'], upgraded['verification_context'])
    assert evaluate(False)['cached_tasks'] == [task['task_id']]


def test_verification_context_refuses_ambiguous_sandbox_settings():
    for value in (None, 0, 1, 'true'):
        with pytest.raises(ValueError, match='sandbox boolean'):
            context.capture([{'task_id': 'policy-control'}], {}, sandbox=value)
