"""Submission orchestration reads the instrument's artifacts, never rescores."""
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from console import submissions as service

ROOT = Path(__file__).resolve().parents[2]
_instances = []


@pytest.fixture(autouse=True)
def close_services():
    yield
    while _instances:
        _instances.pop().close()


def status(tmp_path, **env):
    assert callable(getattr(service, 'scenario_status', None)), 'A5 scenario readiness is missing'
    return service.scenario_status('demo-smoke', platform_root=ROOT,
                                    work_root=tmp_path/'work', driver_env={**os.environ, 'HG_AGENT_BACKEND':'mock', **env})


def test_scenario_real_demo_load_and_isolated_probe(tmp_path):
    result = status(tmp_path)
    assert result['available'] is True and result['reason'] == 'ok'
    assert result['tasks'] == 4
    assert result['expected_minutes'] is None
    assert result['side'] == 'train' and result['dataset'] == 'demo'


def scenario_platform(tmp_path, **changes):
    root = tmp_path/'platform'
    (root/'scenarios').mkdir(parents=True)
    spec = json.loads((ROOT/'scenarios/demo-smoke.json').read_text())
    spec.update(changes)
    (root/'scenarios/demo-smoke.json').write_text(json.dumps(spec))
    return root


@pytest.mark.parametrize('change', [{'rounds':1}, {'limits':{'task_timeout_s':1}}])
def test_incompatible_config_never_claims_available(tmp_path, change):
    root = scenario_platform(tmp_path, **change)
    assert callable(getattr(service, 'scenario_status', None)), 'A5 scenario readiness is missing'
    with pytest.raises(service.SubmissionRejected) as caught:
        service.scenario_status('demo-smoke', platform_root=root, work_root=tmp_path/'work', driver_env={})
    assert caught.value.problems[0]['code'] == 'service-not-ready'


def test_live_requires_configured_actual_backend(tmp_path):
    root = scenario_platform(tmp_path, model={'backend':'live'})
    assert callable(getattr(service, 'scenario_status', None)), 'A5 scenario readiness is missing'
    result = service.scenario_status('demo-smoke', platform_root=root, work_root=tmp_path/'work', driver_env={})
    assert not result['available'] and result['reason'] == 'no-model-endpoint'
    assert result['tasks'] is None


def test_probe_unavailable_is_not_dataset_zero(tmp_path, monkeypatch):
    from eval.containers import sandbox
    def unavailable(*a, **kw):
        raise sandbox.SandboxUnavailable('fixture sandbox unavailable')
    monkeypatch.setattr(sandbox, 'run', unavailable)
    result = status(tmp_path)
    assert not result['available'] and result['reason'] == 'sandbox-unavailable'
    assert result['tasks'] is None


def artifacts(tmp_path, *, invalid=False):
    run = tmp_path/'run'
    (run/'verdicts/round-0').mkdir(parents=True)
    receipt = {'submission_id':'sub-20261030T010000Z-1234abcd', 'run_id':'sub-20261030T010000Z-1234abcd',
               'scenario':{'name':'demo-smoke','side':'train'}, 'scenario_receipt':{'sha256':'scene'},
               'tree_sha256':'tree'}
    meta = {'run_id':receipt['run_id'], 'scenario':{'sha256':'scene'},
            'harness_source':{'sha256':'tree'}, 'platform_content_sha':'platform',
            'framework_version':'test', 'agent_backend':'mock', 'wall_clock_s':12}
    point = {'run_id':receipt['run_id'], 'round':0, 'side':'train', 'score':.5,
             'measured_by_platform':True, 'identity':{'harness_sha':'git-tree', 'agent_backend':'mock'},
             'per_task':{'t1':.5}, 'n_scored':1, 'n_invalid':1 if invalid else 0,
             'n_harness_failed':0, 'n_timeout':0}
    if invalid:
        point['invalid'] = {'t2':{'detail':'fixture environment unavailable'}}
    (run/'run_meta.json').write_text(json.dumps(meta))
    (run/'curve.jsonl').write_text(json.dumps(point)+'\n')
    (run/'verdicts/round-0/t1.json').write_text(json.dumps({'task_id':'t1','round':0,'score':.5}))
    return run, receipt, point


def result(run, receipt):
    assert callable(getattr(service, 'read_submission_result', None)), 'A5 artifact projection is missing'
    return service.read_submission_result(run, receipt=receipt)


def test_result_is_direct_curve_projection_and_invalid_needs_no_verdict(tmp_path):
    run, receipt, _ = artifacts(tmp_path, invalid=True)
    got = result(run, receipt)
    assert got['pools'] == {'train':{'n':2, 'measured':1, 'invalid':1, 'harness_failed':0, 'score':.5}}
    assert got['warnings'] and 'invalid' in ' '.join(got['warnings'])
    assert got['identity']['harness_sha'] == 'git-tree'
    assert got['identity']['platform_content_sha'] == 'platform'


@pytest.mark.parametrize('change', [{'score':float('nan')}, {'measured_by_platform':False},
    {'n_scored':0}, {'n_invalid':None}, {'round':1}, {'round':False}, {'run_id':'other'}, {'side':'eval'}])
def test_result_refuses_unmeasured_or_malformed_points(tmp_path, change):
    run, receipt, point = artifacts(tmp_path)
    point.update(change)
    (run/'curve.jsonl').write_text(json.dumps(point)+'\n')
    assert callable(getattr(service, 'read_submission_result', None)), 'A5 artifact projection is missing'
    with pytest.raises(service.SubmissionRejected):
        result(run, receipt)


def test_result_refuses_wrong_source_and_missing_verdict(tmp_path):
    run, receipt, _ = artifacts(tmp_path)
    assert callable(getattr(service, 'read_submission_result', None)), 'A5 artifact projection is missing'
    with pytest.raises(service.SubmissionRejected):
        result(run, {**receipt,'tree_sha256':'changed'})
    (run/'verdicts/round-0/t1.json').unlink()
    with pytest.raises(service.SubmissionRejected):
        result(run, receipt)


@pytest.mark.parametrize('legacy',[False,True])
def test_result_reads_current_submission_identity_and_old_receipts(tmp_path,legacy):
    run,receipt,_=artifacts(tmp_path)
    meta=json.loads((run/'run_meta.json').read_text())
    meta['harness_source']=({'sha256':'tree'} if legacy else
                            {'submission_sha256':'tree','harness_sha':'git-tree'})
    (run/'run_meta.json').write_text(json.dumps(meta))
    assert result(run,receipt)['identity']['harness_sha']=='git-tree'


@pytest.mark.parametrize('source',[
    {'submission_sha256':'tree','sha256':'other','harness_sha':'git-tree'},
    {'submission_sha256':'other','sha256':'tree','harness_sha':'git-tree'},
    {'sha256':'tree','harness_sha':'different-staged-tree'},
])
def test_result_refuses_conflicting_directory_or_staged_identity(tmp_path,source):
    run,receipt,_=artifacts(tmp_path)
    meta=json.loads((run/'run_meta.json').read_text())
    meta['harness_source']=source
    (run/'run_meta.json').write_text(json.dumps(meta))
    with pytest.raises(service.SubmissionRejected):
        result(run,receipt)


@pytest.mark.parametrize('changes,expected',[
    ({'cumulative':{'wall_clock_s':120}},2),
    ({'cost':{'wall_clock_s':90}},1.5),
    ({},None),
    ({'cumulative':{'wall_clock_s':float('nan')}},None),
    ({'cumulative':{'wall_clock_s':-1}},None),
    ({'cumulative':{'wall_clock_s':120},'measured_by_platform':False},None),
])
def test_scenario_minutes_come_from_original_measured_curve(tmp_path,monkeypatch,changes,expected):
    real_status=service.scenario_status
    instance=runtime(tmp_path,monkeypatch)
    monkeypatch.setattr(service,'scenario_status',real_status)
    run,receipt,point=artifacts(tmp_path)
    scene=hashlib.sha256((ROOT/'scenarios/demo-smoke.json').read_bytes()).hexdigest()
    receipt['scenario_receipt']['sha256']=scene
    receipt.update(status='scored',created_at='2026-11-01T00:00:00Z',scored_at='2026-11-01T00:01:00Z')
    meta=json.loads((run/'run_meta.json').read_text())
    meta['scenario']['sha256']=scene
    meta.pop('wall_clock_s',None)
    (run/'run_meta.json').write_text(json.dumps(meta))
    point.update(changes)
    (run/'curve.jsonl').write_text(json.dumps(point)+'\n')
    run.rename(instance.runs/receipt['run_id'])
    (instance.root/receipt['submission_id']).mkdir()
    instance._save(receipt)
    row=instance.scenarios(driver_env={**os.environ,'HG_AGENT_BACKEND':'mock'})['items'][0]
    assert row['expected_minutes']==expected


def runtime(tmp_path, monkeypatch):
    cls = getattr(service, 'SubmissionService', None)
    assert cls is not None, 'A5 submission runtime is missing'
    for name in ('submissions','runs','budget'):
        (tmp_path/name).mkdir(exist_ok=True)
    instance = cls(platform_root=ROOT, submissions_root=tmp_path/'submissions',
                   runs_root=tmp_path/'runs', work_root=tmp_path/'work', budget_dir=tmp_path/'budget',
                   liveness=lambda:(True, {}))
    _instances.append(instance)
    # These unit cases focus on orchestration; real probe/gate run in separate tests/curl.
    monkeypatch.setattr(service, 'scenario_status', lambda *a,**kw:
                        {'available':True,'reason':'ok','tasks':4,'expected_minutes':None})
    return instance


def wait_for(instance, identity):
    until = time.monotonic()+10
    while time.monotonic() < until:
        value = instance.get(identity)
        if value['status'] in ('failed','scored','rejected'):
            return value
        time.sleep(.01)
    pytest.fail('worker did not reach terminal state')


def test_runtime_background_ack_duplicate_and_failed_artifacts(tmp_path, monkeypatch):
    from tests.quality.test_submission_gate import archive
    instance = runtime(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    real_popen = subprocess.Popen
    class Driver:
        pid = 123456789
        def wait(self):
            entered.set()
            assert release.wait(5)
            return 0  # no artifacts: must become failed, never score=0
    def popen(argv, *a, **kw):
        return Driver() if str(ROOT/'driver.py') in argv else real_popen(argv,*a,**kw)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    upload = archive(tmp_path)
    try:
        code, ack = instance.submit(upload,'demo-smoke',label='probe',contact='private',driver_env={'HG_AGENT_BACKEND':'mock'})
        assert code == 202 and ack['status'] == 'queued' and ack['queue_position'] == 1
        assert entered.wait(3)
        current = instance.get(ack['submission_id'])
        assert current['status'] == 'running' and 'result' not in current and 'contact' not in current
        with pytest.raises(service.SubmissionRejected) as caught:
            instance.submit(upload,'demo-smoke',driver_env={})
        assert caught.value.problems[0]['code'] == 'duplicate-submission'
        other = archive(tmp_path, {'different':b'x'}, name='other.tar.gz')
        with pytest.raises(service.SubmissionRejected) as caught:
            instance.submit(other,'demo-smoke',driver_env={})
        assert caught.value.problems[0]['code'] == 'ambiguous-root'
    finally:
        release.set()
    terminal = wait_for(instance, ack['submission_id'])
    assert terminal['status'] == 'failed' and 'result' not in terminal
    assert instance.list()['items'][0]['score'] is None


def test_pid_receipt_failure_retains_child_until_exit(tmp_path, monkeypatch):
    from tests.quality.test_submission_gate import archive
    instance = runtime(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    real_popen, real_update = subprocess.Popen, instance._update
    class Driver:
        pid = 123456789
        def wait(self):
            entered.set()
            assert release.wait(5)
            return 0
    def popen(argv,*args,**kwargs):
        return Driver() if str(ROOT/'driver.py') in argv else real_popen(argv,*args,**kwargs)
    def update(identity,**changes):
        if 'pid' in changes:
            raise OSError(28,'fixture pid persistence failed')
        return real_update(identity,**changes)
    monkeypatch.setattr(subprocess,'Popen',popen)
    monkeypatch.setattr(instance,'_update',update)
    try:
        _,ack = instance.submit(archive(tmp_path),'demo-smoke',driver_env={'HG_AGENT_BACKEND':'mock'})
        assert entered.wait(.5), 'live child was abandoned without wait'
        assert instance._active == ack['submission_id']
    finally:
        release.set()
    assert wait_for(instance,ack['submission_id'])['status'] == 'failed'


def test_detail_reports_default_files_when_manifest_omits_env_kinds(tmp_path, monkeypatch):
    instance=runtime(tmp_path,monkeypatch)
    identity='sub-20261101T000000Z-1234abcd'
    (instance.root/identity).mkdir()
    instance._save({'submission_id':identity,'run_id':identity,'status':'validating',
                    'scenario':{'name':'demo-smoke','side':'train'},'tree_sha256':'fixture',
                    'harness':{'name':'fixture','version':'1','entrypoint':'agent.py'}})
    assert instance.get(identity)['harness']['declared_env_kinds']==['files']
