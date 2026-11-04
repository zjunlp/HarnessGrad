"""No-model recovery regressions. All scores/identities below are synthetic."""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import appworld_campaign_state as state
from tools import launch_appworld_harness_baselines as launcher
from tools import run_appworld_harness_campaign as campaign


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n')


SYNTHETIC_FILESYSTEM = {'schema': state.FILESYSTEM_SCHEMA, 'sha256': '9'*64, 'entries': 2}
SYNTHETIC_RUNTIME = {'scope': 'synthetic fixture, no external execution', 'environment': {},
                     'files': [], 'harness_filesystem': SYNTHETIC_FILESYSTEM,
                     'harness_source': {'path':'/synthetic/prepared','submission_sha256':'e'*64,'manifest_sha256':'f'*64},
                     'launch_entry':{'python':str(Path(sys.executable).resolve()),'python_sha256':'6'*64,
                                     'driver':str((launcher.ROOT/'driver.py').resolve()),'driver_sha256':'7'*64}}


def mock_reviewed_inputs(identity):
    return (Path(SYNTHETIC_RUNTIME['harness_source']['path']), {},
            {'submission_sha256': identity['harness_sha256']}, identity['manifest_sha256'],
            copy.deepcopy(SYNTHETIC_RUNTIME))


@pytest.fixture
def identity():
    return {'schema': state.IDENTITY_SCHEMA, 'dataset': 'appworld', 'harness': 'miniagent',
            'pool': 'train', 'side': 'train', 'source_sha256': 'a'*64,
            'pool_ids_sha256': 'b'*64, 'scorer_sha256': 'c'*64, 'platform_commit': 'd'*40,
            'harness_sha256': 'e'*64, 'manifest_sha256': 'f'*64, 'runtime_sha256': state.digest(state.runtime_commitment(SYNTHETIC_RUNTIME)),
            'policy': state.POLICY,
            'model': {'backend': 'openai', 'name': 'synthetic-model', 'endpoint': 'http://127.0.0.1:8001/v1',
                      'weights_sha256': '1'*64, 'tokenizer_sha256': '2'*64, 'deployment_sha256': '3'*64},
            'budget': {'deadline_s': 900, 'request_timeout_s': 120, 'output_tokens': 2048,
                       'trials': 1, 'seed': 42, 'jobs': 1, 'native_settings': {}, 'context_policy': 'native-defaults'}}


@pytest.fixture
def selection(identity):
    return {'schema': state.SELECTION_SCHEMA,
            'pools': {p: {'eligible': 30, 'selected': ['a', 'b', 'c']} for p in state.POOLS},
            'identities': {'miniagent:train': identity}}


def metadata(identity, run_id, receipt=None):
    receipt = receipt or Path('/synthetic/receipt')
    m, b = identity['model'], identity['budget']
    return {'run_id': run_id, 'dataset': 'appworld', 'trials': b['trials'], 'jobs': b['jobs'],
            'harness_source': {'submission_sha256': identity['harness_sha256'],
                               'filesystem':copy.deepcopy(SYNTHETIC_FILESYSTEM),
                               'path':SYNTHETIC_RUNTIME['harness_source']['path']},
            'improver':None,
            'config': {'agent': {'backend': m['backend'], 'model': m['name'], 'base_url': m['endpoint']},
                       'run': {'seed': str(b['seed']), 'rounds': 0, 'side': identity['side'],
                               'sandbox':True,'sampling':'all','dataset':'appworld','harness':identity['harness'],
                               'trials':b['trials'],'jobs':b['jobs'],'run_id':run_id,
                               'runs_root':str(receipt/'runs'),
                               'work_root':str(Path('/tmp')/('hg-'+run_id+'-work'))}}}


def task_files(run, tid, score=0.0, *, official=True, invalid=None, exit_code=0):
    row = {'task_id': tid, 'round': 0, 'score': score, 'exit_code': exit_code,
           'process_outcome': 'harness_crash' if exit_code else 'completed'}
    if invalid is not None: row['invalid'] = invalid
    write(run/'progress/round-0'/f'{tid}.json', row)
    if official:
        write(run/'verdicts/round-0'/f'{tid}.json', {
            'task_id': tid, 'round': 0, 'score': score, 'kind': 'command',
            'evidence': {'task_id': tid, 'measured': True, 'num_tests': 2,
                         'source_split': 'train', 'metric': 'appworld.task_goal_completion'}})


def fixture_command(identity, selected, receipt):
    values=state.expected_launch_values(identity,selected,receipt.name,receipt,
                                       state.runtime_commitment(SYNTHETIC_RUNTIME))
    command=[str(Path(sys.executable).resolve()),str((launcher.ROOT/'driver.py').resolve())]
    for key,value in values.items():
        command.append(key)
        if value is not True: command.append(value)
    return command


def attempt(tmp_path, identity, name, tids, *, selected=None):
    receipt = tmp_path/name
    run = receipt/'runs'/name
    frozen = {'schema': state.SELECTION_SCHEMA, 'pools': {p: {'eligible':30,'selected':['a','b','c']} for p in state.POOLS}, 'identities':{'miniagent:train':identity}}
    write(receipt/'launch.json', {'schema':'harnessgrad.appworld-launch.v2', 'selection_sha256':state.digest(frozen), 'run_id': name, 'selected': selected or ['a','b','c'],
                                 'measurement_identity': identity, 'measurement_identity_sha256': state.digest(identity),
                                 'runtime_commitment':state.runtime_commitment(SYNTHETIC_RUNTIME),
                                 'command':fixture_command(identity,selected or ['a','b','c'],receipt)})
    write(run/'run_meta.json', metadata(identity, name, receipt))
    for tid, score in tids.items(): task_files(run, tid, score)
    return {'harness': 'miniagent', 'pool': 'train', 'receipt': str(receipt)}, run


def old_state(selection, attempts):
    return {'schema': state.SCHEMA, 'selection_sha256': state.digest(selection), 'attempts': attempts}


def test_progress_only_discovery_does_not_require_exit_profile_or_curve(tmp_path, identity, selection):
    pointer, run = attempt(tmp_path/'actions', identity, 'appworld-one', {'a': 0})
    old = {'current': {'harness': 'miniagent', 'pool': 'train',
                      'command': ['python', 'launch.py', '--receipts-root', str(tmp_path/'actions')]}}
    assert not (Path(pointer['receipt'])/'exit.json').exists()
    assert not (Path(pointer['receipt'])/'research-profile.json').exists()
    assert not (run/'curve.jsonl').exists()
    assert state.discover_attempts(old) == [pointer]
    recovered = state.recover(selection, old_state(selection, [pointer]))
    assert recovered['coverage']['miniagent:train']['remaining_task_ids'] == ['b','c']
    assert recovered['coverage']['miniagent:train']['per_task'] == {'a':0}
    assert recovered['completed_pools'] == []


def test_progress_score_without_official_verdict_is_not_measured(tmp_path, identity, selection):
    pointer, run = attempt(tmp_path, identity, 'appworld-one', {})
    task_files(run, 'a', 1, official=False)
    recovered = state.recover(selection, old_state(selection, [pointer]))
    c = recovered['coverage']['miniagent:train']
    assert c['measured'] == 0 and c['unmeasured'] == 1 and c['not_recorded'] == 2
    assert c['official_verdict_mean'] is None
    assert not recovered['execution_allowed']


def test_attempt_rows_are_deduplicated_and_disjoint_task_receipts_union(tmp_path, identity, selection):
    a, _ = attempt(tmp_path, identity, 'appworld-one', {'a':0}, selected=['a'])
    b, _ = attempt(tmp_path, identity, 'appworld-two', {'b':1}, selected=['b'])
    recovered = state.recover(selection, old_state(selection, [a,a,b,b]))
    c = recovered['coverage']['miniagent:train']
    assert len(recovered['attempts']) == 2
    assert c['per_task'] == {'a':0,'b':1}
    assert c['remaining_task_ids'] == ['c'] and c['official_verdict_mean'] == .5
    assert not c['measurement_complete']


def test_duplicate_task_attempts_do_not_last_write_or_choose_best(tmp_path, identity, selection):
    a, _ = attempt(tmp_path, identity, 'appworld-one', {'a':0})
    b, _ = attempt(tmp_path, identity, 'appworld-two', {'a':1,'b':0})
    recovered = state.recover(selection, old_state(selection, [a,b]))
    c = recovered['coverage']['miniagent:train']
    assert c['per_task'] == {'b':0} and c['unmeasured'] == 1
    assert c['tasks']['a']['reason'] == 'multiple_attempts_require_review'
    assert not recovered['execution_allowed']


def test_timeout_is_a_separate_class_even_when_curve_has_zero(tmp_path, identity, selection):
    pointer, run = attempt(tmp_path, identity, 'appworld-one', {'a':0})
    task_files(run, 'a', 0, official=False, invalid={'outcome':'timeout'})
    (run/'verdicts/round-0/a.json').unlink()
    write(run/'curve.jsonl', {'round':0, 'per_task':{'a':0}})
    c = state.recover(selection, old_state(selection, [pointer]))['coverage']['miniagent:train']
    assert (c['measured'],c['invalid'],c['timeout'],c['unmeasured'],c['not_recorded']) == (0,0,1,0,2)
    assert c['official_verdict_mean'] is None
    assert c['driver_timeout_inclusive_mean'] == 0
    assert not c['measurement_complete']


def test_nonzero_harness_exit_does_not_discard_official_verdict(tmp_path, identity, selection):
    pointer, run = attempt(tmp_path, identity, 'appworld-one', {})
    task_files(run, 'a', 1, exit_code=1)
    c = state.recover(selection, old_state(selection, [pointer]))['coverage']['miniagent:train']
    assert c['measured'] == 1 and c['per_task'] == {'a':1}
    assert c['tasks']['a']['harness_failed'] is True


def test_invalid_and_curve_score_do_not_double_count(tmp_path, identity, selection):
    pointer, run = attempt(tmp_path, identity, 'appworld-one', {})
    task_files(run, 'a', 1, invalid={'stage':'check','detail':'invalid input'})
    write(run/'curve.jsonl', {'round':0,'per_task':{'a':1}})
    c = state.recover(selection, old_state(selection,[pointer]))['coverage']['miniagent:train']
    assert (c['measured'],c['invalid'],c['not_recorded']) == (0,1,2)
    assert c['tasks']['a']['raw_official_score'] == 1
    assert c['selected'] == sum(c[k] for k in ('measured','invalid','timeout','unmeasured','not_recorded'))
    assert c['eligible'] == 30


@pytest.mark.parametrize('field', ['harness_sha256','scorer_sha256','source_sha256','platform_commit','runtime_sha256'])
def test_changed_code_and_data_identity_refuses_recovery(tmp_path, identity, selection, field):
    changed = copy.deepcopy(identity); changed[field] = '9'*len(changed[field])
    pointer, _ = attempt(tmp_path, changed, 'appworld-one', {'a':1})
    recovered = state.recover(selection, old_state(selection,[pointer]))
    assert not recovered['execution_allowed']
    assert recovered['coverage']['miniagent:train']['per_task'] == {}
    assert any('identity_mismatch' in issue for issue in recovered['issues'])


@pytest.mark.parametrize('field,value', [('name','other'),('endpoint','http://127.0.0.1:8002/v1'),
    ('weights_sha256','4'*64),('tokenizer_sha256','5'*64),('deployment_sha256','6'*64)])
def test_changed_model_binding_refuses(tmp_path, identity, selection, field, value):
    changed=copy.deepcopy(identity); changed['model'][field]=value
    pointer,_=attempt(tmp_path,changed,'appworld-one',{'a':1})
    assert not state.recover(selection,old_state(selection,[pointer]))['execution_allowed']


@pytest.mark.parametrize('field,value', [('deadline_s',300),('request_timeout_s',60),('seed',0),
    ('trials',2),('jobs',24),('native_settings',{'HG_AGENT_MAX_STEPS':'8'}),('context_policy','fixed-32k')])
def test_changed_budget_refuses(tmp_path, identity, selection, field, value):
    changed=copy.deepcopy(identity); changed['budget'][field]=value
    pointer,_=attempt(tmp_path,changed,'appworld-one',{'a':1})
    assert not state.recover(selection,old_state(selection,[pointer]))['execution_allowed']


def test_runtime_meta_mismatch_not_hidden_by_matching_launch(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-one',{'a':1})
    meta=metadata(identity,'appworld-one');meta['config']['agent']['model']='other'
    write(run/'run_meta.json',meta)
    assert not state.recover(selection,old_state(selection,[pointer]))['execution_allowed']


def test_legacy_completed_flag_cannot_skip_nonexistent_pool(tmp_path,identity,selection):
    old={'selection_sha256':state.digest(selection),'completed':[{'harness':'miniagent','pool':'train','receipt':str(tmp_path/'absent'),'exit_code':0}]}
    recovered=state.recover(selection,old)
    assert not recovered['execution_allowed'] and recovered['completed_pools']==[]
    assert recovered['coverage']['miniagent:train']['remaining_task_ids']==['a','b','c']


def test_legacy_missing_identity_remains_readonly_review(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-one',{'a':1})
    write(Path(pointer['receipt'])/'launch.json',{'run_id':'appworld-one','selected':['a','b','c']})
    recovered=state.recover(selection,{'completed':[pointer,pointer], 'inherited_completed':[pointer]})
    assert len(recovered['attempts'])==1 and not recovered['execution_allowed']
    assert recovered['coverage']['miniagent:train']['per_task']=={}


def test_receipt_changes_after_recovery_are_detected(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-one',{'a':1})
    old=state.recover(selection,old_state(selection,[pointer]))
    task_files(run,'a',0)
    recovered=state.recover(selection,old)
    assert any('evidence_changed' in x for x in recovered['issues'])
    assert not recovered['execution_allowed']


def test_live_attempt_lock_refuses_resume(tmp_path,identity,selection):
    import fcntl
    pointer,_=attempt(tmp_path,identity,'appworld-one',{'a':1})
    with (Path(pointer['receipt'])/'.attempt.lock').open('w') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        with pytest.raises(state.RecoveryError,match='still live'):
            state.recover(selection,old_state(selection,[pointer]))


def test_outside_cohort_evidence_never_expands_coverage(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-one',{'a':1,'outside':1})
    recovered=state.recover(selection,old_state(selection,[pointer]))
    assert not recovered['execution_allowed']
    assert 'outside' not in recovered['coverage']['miniagent:train']['tasks']


def mock_current(monkeypatch, identity):
    monkeypatch.setattr(campaign,'validate_selection',lambda *a:None)
    monkeypatch.setattr(campaign,'current_identity',lambda expected,bindings:copy.deepcopy(expected))
    monkeypatch.setattr(launcher,'current_identity',lambda expected,bindings:copy.deepcopy(expected))
    monkeypatch.setattr(launcher,'reviewed_harness_inputs',lambda identity,bindings:mock_reviewed_inputs(identity))
    monkeypatch.setattr(launcher,'ensure_frozen',lambda:None)
    monkeypatch.setattr(launcher,'observe_service',lambda _:None)


def test_two_interruptions_then_complete_through_campaign_launcher(tmp_path,monkeypatch,identity,selection):
    """Call the real launcher main; only model driver execution is replaced."""
    mock_current(monkeypatch,identity)
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL', '1')
    sp=tmp_path/'selection.json'; bp=tmp_path/'bindings.json'
    write(sp,selection);write(bp,{})
    starts=[]
    from eval.run import runner
    from eval.observe.tracing import _write_progress
    repo=tmp_path/'synthetic-candidate';repo.mkdir()
    write(repo/'harness.json',{'name':'synthetic','version':'1','entrypoint':'agent.py','env_kinds':['files']})
    (repo/'agent.py').write_text('raise RuntimeError("must not execute")')
    def synthetic_result(_repo, task, **kwargs):
        tid=task['task_id'];score=1 if tid=='b' else 0
        return {'task_id':tid, 'answer':'', 'trace':'', 'exit_code':0, 'stderr':'',
                'verdict':{'kind':'command', 'score':score, 'evidence':{
                    'task_id':tid, 'measured':True, 'source_split':'train',
                    'metric':'appworld.task_goal_completion', 'num_tests':2}},
                'env_usage':{}, 'model_gateway':None, 'identity':None}
    monkeypatch.setattr(runner,'run_one',synthetic_result)
    def persist_then_interrupt(*args,**kwargs):
        _write_progress(*args,**kwargs)
        if len(starts)<3:
            raise KeyboardInterrupt('synthetic interruption between completed tasks')
    monkeypatch.setattr(runner,'_write_progress',persist_then_interrupt)
    def simulated_driver(cmd,actual,bindings,log,lock_fd):
        selected=cmd[cmd.index('--tasks')+1].split(','); starts.append(selected)
        run_id=cmd[cmd.index('--run-id')+1];run=Path(cmd[cmd.index('--runs-root')+1])/run_id
        write(run/'run_meta.json',metadata(actual,run_id,run.parent.parent))
        # Actual evaluate/start/verdict/progress chain, no end-of-round verdicts.
        runner.evaluate(repo,[{'task_id':tid} for tid in selected],{},sandbox=False,
                        progress_dir=run,run_id=run_id)
        assert not (run/'verdicts').exists()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher,'run_driver',simulated_driver)
    def invoke_launcher(cmd):
        return SimpleNamespace(returncode=launcher.main(cmd[2:]))
    monkeypatch.setattr(campaign,'run_launcher',invoke_launcher)
    def args(output,previous=None):
        argv=['--selection',str(sp),'--bindings',str(bp),'--output',str(output),'--harness','miniagent',
              '--pools','train','--review-note','synthetic fixture','--operator-authorization','synthetic fixture','--execute']
        if previous: argv+=['--resume-status',str(previous/'status.json')]
        return argv
    a,b,c=[tmp_path/x for x in ('first','second','third')]
    with pytest.raises(KeyboardInterrupt):campaign.main(args(a))
    one=state.read(a/'status.json');receipt=Path(one['attempts'][0]['receipt'])
    assert not (receipt/'exit.json').exists()
    with pytest.raises(KeyboardInterrupt):campaign.main(args(b,a))
    two=state.read(b/'status.json');assert len(two['attempts'])==2
    assert two['completed_pools']==[]
    assert campaign.main(args(c,b))==0
    final=state.read(c/'status.json');cov=final['coverage']['miniagent:train']
    assert starts==[['a','b','c'],['b','c'],['c']]
    assert len(final['attempts'])==3 and final['completed_pools']==['miniagent:train']
    assert cov['per_task']=={'a':0,'b':1,'c':0} and cov['measured']==3
    assert cov['official_verdict_mean']==pytest.approx(1/3)
    assert 'inherited_completed' not in final and 'completed' not in final


def test_dry_run_creates_nothing_and_launch_is_not_called(tmp_path,monkeypatch,identity,selection,capsys):
    mock_current(monkeypatch,identity)
    sp,bp=tmp_path/'selection.json',tmp_path/'bindings.json';write(sp,selection);write(bp,{})
    monkeypatch.setattr(campaign,'run_launcher',lambda cmd:pytest.fail('must not launch'))
    output=tmp_path/'output'
    assert campaign.main(['--selection',str(sp),'--bindings',str(bp),'--output',str(output),'--harness','miniagent',
        '--pools','train','--review-note','synthetic','--operator-authorization','synthetic'])==0
    assert not output.exists()
    assert json.loads(capsys.readouterr().out)['actions'][0]['task_ids']==['a','b','c']


def test_bound_native_settings_replace_stale_inherited_settings(monkeypatch,identity):
    monkeypatch.setenv('HG_AGENT_CONTEXT_WINDOW','16384')
    monkeypatch.setenv('HG_MINISWE_STEP_LIMIT','10')
    monkeypatch.setenv('HG_AGENT_BASE_URL','http://wrong')
    monkeypatch.setattr(launcher,'dataset_environment',lambda: {'HG_DATA_APPWORLD':'/synthetic/data'})
    source=launcher.ROOT/'base_harness'/identity['harness']
    runtime={'environment':{}, 'harness_filesystem':launcher.filesystem_identity(source),
             'harness_source':{'path':str(source.resolve()),
                               'submission_sha256':launcher.source_identity(source)['submission_sha256'],
                               'manifest_sha256':state.file_hash(source/'harness.json')},
             'launch_entry':{'python':str(Path(sys.executable).resolve()),'python_sha256':state.file_hash(Path(sys.executable)),
                             'driver':str((launcher.ROOT/'driver.py').resolve()),'driver_sha256':state.file_hash(launcher.ROOT/'driver.py')}}
    identity['runtime_sha256']=state.digest(state.runtime_commitment(runtime))
    monkeypatch.setattr(launcher,'runtime_binding',lambda *a:runtime)
    monkeypatch.setattr(launcher,'validate_appworld_overlay',lambda *a:None)
    monkeypatch.setenv('HG_APPWORLD_STUDY_IDS','/host/study.csv')
    monkeypatch.setenv('DSH_PERMISSION_MODE','danger-full-access')
    monkeypatch.setenv('OPENCLAW_CONFIG_PATH','/host/config')
    monkeypatch.setenv('PYTHONPATH','/host/unknown-code')
    env=launcher.execution_environment(identity,{'harness_runtimes':{'miniagent':{}}})
    assert all(key not in env for key in ('HG_APPWORLD_STUDY_IDS','DSH_PERMISSION_MODE','OPENCLAW_CONFIG_PATH','PYTHONPATH'))
    assert 'HG_AGENT_CONTEXT_WINDOW' not in env and 'HG_MINISWE_STEP_LIMIT' not in env
    assert env['HG_AGENT_BASE_URL']==identity['model']['endpoint']
    cmd=launcher.command_for(identity,['a'],Path('/tmp/appworld-synthetic'))
    assert cmd[cmd.index('--seed')+1]=='42'
    assert '--harness-path' in cmd and '--fail-on-incomplete' in cmd


def test_unknown_model_identity_cannot_resume(identity):
    identity['model']['weights_sha256']=None
    with pytest.raises(state.RecoveryError,match='identity unknown'):
        state.validate_identity(identity)


def test_started_task_without_terminal_receipt_requires_review(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-one',{'a':0})
    write(run/'events.jsonl', {'kind':'task','round':0,'task':'b','status':'start'})
    recovered=state.recover(selection,old_state(selection,[pointer]))
    c=recovered['coverage']['miniagent:train']
    assert c['tasks']['b']['status']=='unmeasured'
    assert c['remaining_task_ids']==['c'] and not recovered['execution_allowed']


def test_legacy_selection_default_is_audit_only(tmp_path,capsys):
    sp,old=tmp_path/'selection.json',tmp_path/'old.json'
    write(sp,{'context_policy':'native-defaults'})
    write(old,{'completed':[{'harness':'miniagent','pool':'train','receipt':str(tmp_path/'missing')}]})
    args=['--selection',str(sp),'--bindings',str(tmp_path/'absent-bindings.json'),
          '--output',str(tmp_path/'out'),'--harness','miniagent','--resume-status',str(old),
          '--review-note','synthetic','--operator-authorization','synthetic']
    assert campaign.main(args)==0
    audit=json.loads(capsys.readouterr().out)
    assert not audit['execution_allowed'] and audit['completed_pools']==[]
    with pytest.raises(SystemExit) as exc:campaign.main(args+['--execute'])
    assert exc.value.code==2 and not (tmp_path/'out').exists()


def test_driver_inherits_attempt_lock(monkeypatch,identity,tmp_path):
    captured={}
    def fake_run(cmd,**kwargs):
        captured.update(kwargs);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher.subprocess,'run',fake_run)
    monkeypatch.setattr(launcher,'execution_environment',lambda *a:{})
    with (tmp_path/'lock').open('w') as lock, (tmp_path/'log').open('w') as log:
        launcher.run_driver(['synthetic-driver'],identity,{},log,lock.fileno())
        assert captured['pass_fds']==(lock.fileno(),)


def test_current_identity_reads_actual_code_data_and_scorer_files(tmp_path,monkeypatch,identity):
    code=tmp_path/'code'; h=code/'base_harness/miniagent';h.mkdir(parents=True)
    (code/'driver.py').write_text('# synthetic driver')
    write(h/'harness.json', {'name':'miniagent','version':'1','entrypoint':'agent.py','backend':'cli','env_kinds':['exec']})
    (h/'agent.py').write_text('print("synthetic")\n')
    verify=code/'data/datasets/appworld/verify';verify.mkdir(parents=True)
    (verify/'check.py').write_text('# synthetic scorer\n')
    bundle=tmp_path/'bundle';bundle.write_bytes(b'synthetic dataset')
    monkeypatch.setattr(launcher,'ROOT',code)
    dataset=SimpleNamespace(BUNDLE=bundle, split_ids=lambda _: {'train':['a','b','c']},
        IMAGE='sha256:'+'1'*64, SERVICE_IMAGE='sha256:'+'2'*64, VERIFIER_IMAGE='sha256:'+'3'*64)
    monkeypatch.setitem(sys.modules,'data.datasets.appworld',dataset)
    monkeypatch.setattr(launcher,'preflight_appworld_data',lambda:None)
    monkeypatch.setattr(launcher.subprocess,'check_output',lambda *a,**k:'d'*40+'\n')
    bindings={'model':identity['model'],'harness_runtimes':{'miniagent':{'files':[],'environment':{}}}}
    baseline=launcher.current_identity(identity,bindings)
    launcher.check_identity(baseline,launcher.current_identity(baseline,bindings))
    for path in (h/'agent.py',verify/'check.py',bundle):
        original=path.read_bytes();path.write_bytes(original+b'changed')
        with pytest.raises(state.RecoveryError,match='mismatch'):
            launcher.check_identity(baseline,launcher.current_identity(baseline,bindings))
        path.write_bytes(original)
    bindings['model']={**bindings['model'],'deployment_sha256':'7'*64}
    with pytest.raises(state.RecoveryError,match='model deployment'):
        launcher.current_identity(baseline,bindings)


def test_launch_producer_checks_identity_before_creating_attempt(tmp_path,monkeypatch,identity,selection):
    sp,bp=tmp_path/'selection.json',tmp_path/'bindings.json';write(sp,selection);write(bp,{})
    changed=copy.deepcopy(identity);changed['harness_sha256']='9'*64
    monkeypatch.setattr(launcher,'current_identity',lambda *a:changed)
    monkeypatch.setattr(launcher,'run_driver',lambda *a:pytest.fail('model must not run'))
    receipt=tmp_path/'appworld-forbidden'
    with pytest.raises(SystemExit) as exc:
        launcher.main(['--selection',str(sp),'--bindings',str(bp),'--harness','miniagent',
                       '--pool','train','--task-ids','a','--receipt-dir',str(receipt),'--execute'])
    assert exc.value.code==2 and not receipt.exists()


def test_launcher_dry_run_never_probes_or_writes(tmp_path,monkeypatch,identity,selection,capsys):
    monkeypatch.setattr(launcher,'reviewed_harness_inputs',lambda identity,bindings:mock_reviewed_inputs(identity))
    sp,bp=tmp_path/'selection.json',tmp_path/'bindings.json';write(sp,selection);write(bp,{})
    monkeypatch.setattr(launcher,'current_identity',lambda *a:copy.deepcopy(identity))
    monkeypatch.setattr(launcher,'observe_service',lambda *a:pytest.fail('no network in plan'))
    receipt=tmp_path/'appworld-plan'
    assert launcher.main(['--selection',str(sp),'--bindings',str(bp),'--harness','miniagent',
                          '--pool','train','--task-ids','b,a','--receipt-dir',str(receipt)])==0
    plan=json.loads(capsys.readouterr().out)
    assert plan['selected']==['a','b'] and not receipt.exists()
    assert plan['measurement_identity_sha256']==state.digest(identity)


def test_conflicting_progress_and_official_verdict_block_resume(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-one',{'a':1})
    write(run/'progress/round-0/a.json',{'task_id':'a','round':0,'score':0})
    recovered=state.recover(selection,old_state(selection,[pointer]))
    assert not recovered['execution_allowed']
    assert any('progress_verdict_conflict' in issue for issue in recovered['issues'])


def test_failed_attempt_partial_cannot_enter_completed_pools(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-one',{'a':1})
    task_files(run,'b',0,official=False,invalid={'stage':'provider','detail':'synthetic request error'})
    write(Path(pointer['receipt'])/'exit.json',{'exit_code':2})
    recovered=state.recover(selection,old_state(selection,[pointer]))
    again=state.recover(selection,recovered)
    c=again['coverage']['miniagent:train']
    assert c['per_task']=={'a':1} and c['invalid']==1 and c['remaining_task_ids']==['c']
    assert again['completed_pools']==[] and not again['execution_allowed']


def test_runtime_file_replacement_at_same_path_is_refused(tmp_path):
    binary=tmp_path/'bin/native'; binary.parent.mkdir(); binary.write_bytes(b'v1')
    binding={'files':[{'path':str(binary),'sha256':state.file_hash(binary)}],'environment':{'HG_AGENT_CODEX':str(binary)}}
    manifest={'runtime_paths':[str(binary.parent)]}
    observed=launcher.runtime_binding(manifest,binding)
    assert observed['files'][0]['sha256']==state.file_hash(binary)
    binary.write_bytes(b'v2')
    with pytest.raises(state.RecoveryError,match='runtime file content mismatch'):
        launcher.runtime_binding(manifest,binding)


def test_runtime_inventory_must_cover_declared_roots_and_configured_binary(tmp_path):
    binary=tmp_path/'bin/native'; binary.parent.mkdir(); binary.write_bytes(b'v1')
    with pytest.raises(state.RecoveryError,match='no reviewed'):
        launcher.runtime_binding({'runtime_paths':[str(binary.parent)]},{'files':[],'environment':{}})
    with pytest.raises(state.RecoveryError,match='outside the inventory'):
        launcher.runtime_binding({}, {'files':[], 'environment':{'HG_AGENT_CODEX':str(binary)}})
    with pytest.raises(state.RecoveryError,match='PROVENANCE'):
        launcher.runtime_binding({'install':'requirements.txt'}, {'files':[],'environment':{}})


def test_journal_without_progress_survives_publication_interruption(tmp_path, identity, selection):
    from eval.observe import task_verdicts
    pointer, run = attempt(tmp_path, identity, 'appworld-journal', {})
    verdict = {'kind':'command', 'score':1, 'evidence':{
        'task_id':'a', 'source_split':'train', 'measured':True,
        'metric':'appworld.task_goal_completion', 'num_tests':2}}
    task_verdicts.save(run, 0, 'a', [{'verdict':verdict, 'exit_code':1, 'stderr':''}])
    recovered = state.recover(selection, old_state(selection, [pointer]))
    cov = recovered['coverage']['miniagent:train']
    assert cov['per_task'] == {'a':1} and cov['harness_nonzero_exit'] == 1
    assert cov['remaining_task_ids'] == ['b','c']
    assert recovered['execution_allowed']


@pytest.mark.parametrize('mutation', ['hash', 'binding', 'symlink', 'final-conflict'])
def test_journal_cannot_be_replaced_or_conflict_with_final(tmp_path, identity, selection, mutation, monkeypatch):
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL', '1')
    from eval.observe.tracing import _write_progress
    pointer, run = attempt(tmp_path, identity, 'appworld-journal', {})
    verdict = {'kind':'command', 'score':1, 'evidence':{
        'task_id':'a', 'source_split':'train', 'measured':True,
        'metric':'appworld.task_goal_completion', 'num_tests':2}}
    _write_progress(run, 0, 'a', [{'verdict':verdict, 'exit_code':0, 'trace':'', 'stderr':''}], None)
    progress = state.read(run/'progress/round-0/a.json')
    path = run/progress['verdict_receipt']['path']
    if mutation == 'hash':
        path.write_text(path.read_text()+' ')
    elif mutation == 'binding':
        record = state.read(path);record['run_id']='other';write(path,record)
    elif mutation == 'symlink':
        outside=tmp_path/'other.json';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
    else:
        task_files(run,'a',0)
    recovered=state.recover(selection,old_state(selection,[pointer]))
    assert not recovered['execution_allowed']
    assert recovered['coverage']['miniagent:train']['per_task'] == {}


def test_platform_timeout_with_raw_verdict_stays_disjoint(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-timeout',{'a':0})
    row=state.read(run/'progress/round-0/a.json');row['process_outcome']='platform_timeout'
    write(run/'progress/round-0/a.json',row)
    c=state.recover(selection,old_state(selection,[pointer]))['coverage']['miniagent:train']
    assert (c['measured'],c['timeout'],c['invalid'])==(0,1,0)
    assert c['tasks']['a']['raw_official_score']==0
    assert c['official_verdict_mean'] is None


def test_started_journal_without_events_or_terminal_cannot_be_retried(tmp_path,identity,selection):
    from eval.observe import task_verdicts
    pointer,run=attempt(tmp_path,identity,'appworld-started',{})
    task_verdicts.start(run,0,'a')
    recovered=state.recover(selection,old_state(selection,[pointer]))
    cov=recovered['coverage']['miniagent:train']
    assert cov['tasks']['a']['status']=='unmeasured'
    assert cov['remaining_task_ids']==['b','c']
    assert not recovered['execution_allowed']


def test_opted_in_real_runner_marks_before_native_execution(tmp_path,monkeypatch):
    from eval.run import runner
    from eval.observe import task_verdicts
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL','1')
    repo=tmp_path/'candidate';repo.mkdir()
    write(repo/'harness.json',{'name':'synthetic','version':'1','entrypoint':'agent.py','env_kinds':['files']})
    (repo/'agent.py').write_text('raise RuntimeError("must not execute")')
    run=tmp_path/'synthetic-run'
    called=[]
    def interruption(*args,**kwargs):
        marker=run/'task-starts/round-0'/task_verdicts.relative_path(0,'a').name
        assert state.read(marker)['task_id']=='a'
        called.append('a')
        raise KeyboardInterrupt('synthetic interruption after durable start')
    monkeypatch.setattr(runner,'run_one',interruption)
    with pytest.raises(KeyboardInterrupt):
        runner.evaluate(repo,[{'task_id':'a','goal':'synthetic'}],{},sandbox=False,
                        progress_dir=run,run_id=run.name)
    assert called==['a']
    assert not (run/'progress/round-0/a.json').exists()


def test_interrupted_atomic_progress_keeps_only_complete_verdict(tmp_path,identity,selection,monkeypatch):
    from eval.observe import task_verdicts
    from eval.observe.tracing import _write_progress
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL','1')
    pointer,run=attempt(tmp_path,identity,'appworld-write-interrupted',{})
    verdict={'kind':'command','score':1,'evidence':{
        'task_id':'a','source_split':'train','measured':True,
        'metric':'appworld.task_goal_completion','num_tests':2}}
    monkeypatch.setattr(task_verdicts.os,'replace',lambda *_: (_ for _ in ()).throw(OSError('synthetic write interruption')))
    with pytest.raises(OSError,match='write interruption'):
        _write_progress(run,0,'a',[{'verdict':verdict,'exit_code':0,'stderr':''}],None)
    assert not (run/'progress/round-0/a.json').exists()
    recovered=state.recover(selection,old_state(selection,[pointer]))
    assert recovered['coverage']['miniagent:train']['per_task']=={'a':1}
    assert recovered['execution_allowed']


def test_required_verdict_journal_write_failure_is_fatal(tmp_path,monkeypatch):
    from eval.observe import task_verdicts
    from eval.observe.tracing import _write_progress
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL','1')
    monkeypatch.setattr(task_verdicts,'save',lambda *_: (_ for _ in ()).throw(OSError('synthetic journal full')))
    with pytest.raises(OSError,match='journal full'):
        _write_progress(tmp_path,0,'a',[{'verdict':{'score':1},'exit_code':0}],None)
    assert not (tmp_path/'progress/round-0/a.json').exists()


@pytest.mark.parametrize('fields', [
    {'exit_code':1}, {'process_outcome':'platform_timeout'},
])
def test_progress_cannot_change_journal_process_outcome(tmp_path,identity,selection,monkeypatch,fields):
    from eval.observe.tracing import _write_progress
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL','1')
    pointer,run=attempt(tmp_path,identity,'appworld-process-conflict',{})
    verdict={'kind':'command','score':1,'evidence':{
        'task_id':'a','source_split':'train','measured':True,
        'metric':'appworld.task_goal_completion','num_tests':2}}
    _write_progress(run,0,'a',[{'verdict':verdict,'exit_code':0,'stderr':''}],None)
    path=run/'progress/round-0/a.json';row=state.read(path);row.update(fields);write(path,row)
    recovered=state.recover(selection,old_state(selection,[pointer]))
    assert not recovered['execution_allowed']
    assert any('journal_progress_process_conflict' in value for value in recovered['issues'])


def test_run_directory_must_match_launch_identity(tmp_path,identity,selection):
    from eval.observe import task_verdicts
    pointer,run=attempt(tmp_path,identity,'appworld-expected',{})
    renamed=run.with_name('appworld-unexpected');run.rename(renamed)
    verdict={'kind':'command','score':1,'evidence':{
        'task_id':'a','source_split':'train','measured':True,
        'metric':'appworld.task_goal_completion','num_tests':2}}
    task_verdicts.save(renamed,0,'a',[{'verdict':verdict,'exit_code':0,'stderr':''}])
    recovered=state.recover(selection,old_state(selection,[pointer]))
    assert not recovered['execution_allowed']
    assert any('run_directory_identity_mismatch' in value for value in recovered['issues'])


@pytest.mark.parametrize('tamper', ['no-commitment','old-runtime-algorithm','commitment-hash',
    'commitment-shape','no-fs-flag','duplicate-fs-flag','different-fs-flag','no-source-flag',
    'no-meta-fs','meta-fs-hash','meta-fs-count','meta-fs-shape','coordinated-meta-command'])
def test_recovery_requires_reviewed_filesystem_contract(tmp_path,identity,selection,tamper):
    pointer,run=attempt(tmp_path,identity,'appworld-filesystem',{'a':1})
    launch_path=Path(pointer['receipt'])/'launch.json';launch=state.read(launch_path)
    meta_path=run/'run_meta.json';meta=state.read(meta_path)
    if tamper=='no-commitment': launch.pop('runtime_commitment')
    elif tamper=='old-runtime-algorithm':
        launch['measurement_identity']['runtime_sha256']=state.digest(SYNTHETIC_RUNTIME)
        launch['measurement_identity_sha256']=state.digest(launch['measurement_identity'])
    elif tamper=='commitment-hash': launch['runtime_commitment']['base_runtime_sha256']='8'*64
    elif tamper=='commitment-shape': launch['runtime_commitment']['unreviewed']='extra'
    elif tamper=='no-fs-flag':
        index=launch['command'].index('--expected-harness-filesystem-sha256');del launch['command'][index:index+2]
    elif tamper=='duplicate-fs-flag': launch['command'].append('--expected-harness-filesystem-sha256='+'9'*64)
    elif tamper=='different-fs-flag': launch['command'][launch['command'].index('--expected-harness-filesystem-sha256')+1]='8'*64
    elif tamper=='no-source-flag':
        index=launch['command'].index('--expected-harness-sha256');del launch['command'][index:index+2]
    elif tamper=='no-meta-fs': meta['harness_source'].pop('filesystem')
    elif tamper=='meta-fs-hash': meta['harness_source']['filesystem']['sha256']='8'*64
    elif tamper=='meta-fs-count': meta['harness_source']['filesystem']['entries']=True
    elif tamper=='meta-fs-shape': meta['harness_source']['filesystem']['extra']='not reviewed'
    else:
        launch['command'][launch['command'].index('--expected-harness-filesystem-sha256')+1]='8'*64
        launch['runtime_commitment']['filesystem']['sha256']='8'*64
        meta['harness_source']['filesystem']['sha256']='8'*64
    write(launch_path,launch);write(meta_path,meta)
    result=state.recover(selection,old_state(selection,[pointer]))
    assert not result['execution_allowed']
    assert result['attempt_evidence'][0]['identity_status']=='unverified'
    coverage=result['coverage']['miniagent:train']
    assert coverage['measured']==0 and coverage['per_task']=={}


def test_incomplete_old_attempt_without_runtime_or_run_is_unverified(tmp_path,identity,selection):
    pointer,run=attempt(tmp_path,identity,'appworld-incomplete',{})
    launch_path=Path(pointer['receipt'])/'launch.json';launch=state.read(launch_path)
    launch.pop('runtime_commitment');write(launch_path,launch)
    import shutil
    shutil.rmtree(run)
    result=state.read_attempt(pointer,identity,['a','b','c'],state.digest(selection))
    assert result['identity_status']=='unverified'
    assert 'run_directory_missing' in result['issues']


@pytest.mark.parametrize('tamper',['sandbox-false','sampling-other','harness-path-replaced',
    'duplicate-harness-path','no-sandbox-argv','abbrev-fs','abbrev-source','abbrev-harness',
    'duplicate-seed','different-tasks','different-side','different-runs-root','different-work-root',
    'different-improver','unknown-config','committed-source-path','meta-source-relative','wrong-python','wrong-driver','entry-hash'])
def test_strict_recovery_rejects_effective_command_or_metadata_drift(tmp_path,identity,selection,tamper):
    pointer,run=attempt(tmp_path,identity,'appworld-strict-contract',{'a':1})
    lp=Path(pointer['receipt'])/'launch.json';launch=state.read(lp)
    mp=run/'run_meta.json';meta=state.read(mp)
    if tamper=='wrong-python': launch['command'][0]='/synthetic/unreviewed-python'
    elif tamper=='wrong-driver': launch['command'][1]='/synthetic/evil/driver.py'
    elif tamper=='entry-hash': launch['runtime_commitment']['entry']['python_sha256']='8'*64
    elif tamper=='sandbox-false': meta['config']['run']['sandbox']=False
    elif tamper=='sampling-other': meta['config']['run']['sampling']='pilot_10pct'
    elif tamper=='harness-path-replaced': meta['harness_source']['path']='/synthetic/other'
    elif tamper=='duplicate-harness-path': launch['command']+=['--harness-path','/synthetic/other']
    elif tamper=='no-sandbox-argv':
        launch['command']+=['--no-sandbox'];meta['config']['run']['sandbox']=False
    elif tamper.startswith('abbrev-'):
        option={'abbrev-fs':'--expected-harness-filesystem-sha','abbrev-source':'--expected-harness-sha',
                'abbrev-harness':'--harness-p'}[tamper]
        launch['command']+=[option+'='+'8'*64]
    elif tamper=='duplicate-seed': launch['command']+=['--seed=42']
    elif tamper.startswith('different-'):
        key,value={'different-tasks':('--tasks','a,b'), 'different-side':('--side','eval'),
            'different-runs-root':('--runs-root','/synthetic/other-runs'),
            'different-work-root':('--work-root','/synthetic/other-work'),
            'different-improver':('--improver','other')}[tamper]
        launch['command'][launch['command'].index(key)+1]=value
    elif tamper=='unknown-config': launch['command']+=['--config','/synthetic/override.json']
    elif tamper=='committed-source-path':
        launch['runtime_commitment']['source']['path']='/synthetic/other'
        launch['command'][launch['command'].index('--harness-path')+1]='/synthetic/other'
        meta['harness_source']['path']='/synthetic/other'
    else: meta['harness_source']['path']='relative/source'
    write(lp,launch);write(mp,meta)
    result=state.recover(selection,old_state(selection,[pointer]))
    assert not result['execution_allowed']
    assert result['attempt_evidence'][0]['identity_status']=='unverified'
    assert result['coverage']['miniagent:train']['measured']==0


def test_negative_reviewed_seed_roundtrips_strict_command_and_recovery(tmp_path,identity,selection):
    identity['budget']['seed']=-7
    state.validate_identity(identity)
    pointer,run=attempt(tmp_path,identity,'appworld-negative-seed',{'a':1})
    result=state.recover(selection,old_state(selection,[pointer]))
    assert result['execution_allowed']
    assert result['attempt_evidence'][0]['identity_status']=='matched'
    assert result['coverage']['miniagent:train']['per_task']=={'a':1}
