"""Read-only UAT must inspect the real service without resuming its jobs."""
import importlib.util
import json
import copy
import os
from pathlib import Path
import socket
import shutil
import urllib.request

import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def smoke():
    spec=importlib.util.spec_from_file_location('submission_uat',ROOT/'console/uat/smoke.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def roots(tmp_path):
    for name in ('runs','submissions'):
        (tmp_path/name).mkdir()
    return tmp_path


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def test_self_started_console_is_read_only_and_uses_explicit_roots(smoke,roots,monkeypatch):
    identity='sub-20261030T000000Z-1234abcd'
    record=roots/'submissions'/identity
    record.mkdir()
    receipt={'submission_id':identity,'run_id':identity,'status':'queued',
             'scenario':{'name':'demo-smoke'},'created_at':'2026-10-30T00:00:00Z'}
    (record/'receipt.json').write_text(json.dumps(receipt))
    expired='sub-20261030T000000Z-5678abcd'
    (roots/'submissions'/expired).mkdir()
    (roots/'submissions'/expired/'receipt.json').write_text(json.dumps({
        **receipt,'submission_id':expired,'run_id':expired,'status':'failed',
        'expires_at':'2020-01-01T00:00:00Z'}))
    (roots/'runs'/expired).mkdir()
    (roots/'runs'/expired/'keep').write_text('not swept')
    before={str(p.relative_to(roots)):p.read_bytes() for p in roots.rglob('*') if p.is_file()}
    monkeypatch.setenv('HG_CONSOLE_SUBMISSIONS_ROOT',str(roots/'wrong'))
    port=free_port()
    process=None
    try:
        process=smoke.start_server(port,runs_root=roots/'runs',platform_root=ROOT,
                                   submissions_root=roots/'submissions')
        base=f'http://127.0.0.1:{port}'
        with urllib.request.urlopen(base+'/api/health') as response:
            assert Path(json.load(response)['runs_root']).resolve()==(roots/'runs').resolve()
        with urllib.request.urlopen(base+'/api/submissions') as response:
            assert json.load(response)['items'][0]['submission_id']==identity
        for method in ('POST','DELETE','PUT','PATCH'):
            request=urllib.request.Request(base+'/api/submissions',data=b'not a job',method=method)
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            assert caught.value.code==405
        assert not (roots/'submissions/.control').exists()
        assert before=={str(p.relative_to(roots)):p.read_bytes() for p in roots.rglob('*') if p.is_file()}
        assert os.environ['HG_CONSOLE_SUBMISSIONS_ROOT']==str(roots/'wrong')
    finally:
        smoke.stop_server(process)
    assert smoke.port_free(port)


@pytest.fixture
def submitted(smoke,roots,monkeypatch):
    from test_submission_runtime import artifacts
    from console.submissions import read_submission_result
    source,receipt,_=artifacts(roots/'fixture',invalid=True)
    identity=receipt['submission_id']
    shutil.copytree(source,roots/'runs'/identity)
    receipt.update(status='scored',exit_code=0,created_at='2026-10-30T00:00:00Z')
    (roots/'submissions'/identity).mkdir()
    (roots/'submissions'/identity/'receipt.json').write_text(json.dumps(receipt))
    (roots/'submissions'/identity/'driver.log').write_bytes(b'only this submission\n')
    result=read_submission_result(roots/'runs'/identity,receipt=receipt)
    detail={'api_version':'1','submission_id':identity,'run_id':identity,'status':'scored',
            'scenario':receipt['scenario'],'queue_position':None,'result':result}
    uat=smoke.UAT('http://127.0.0.1:4470',roots/'runs',1000,roots/'evidence',
                  submission_id=identity,submissions_root=roots/'submissions')
    uat.health={'runs_root':str(roots/'runs')}
    def get(path):
        if path=='/api/scenarios':
            data={'api_version':'1','items':[{'name':'demo-smoke','available':True,'reason':'ok',
                  'tasks':4,'expected_minutes':None}]}
        elif path=='/api/submissions':
            data={'api_version':'1','items':[{'submission_id':identity,'status':'scored','score':None,
                  'pools':{'train':.5}}],'next_cursor':None}
        elif path=='/api/submissions/'+identity:
            data=copy.deepcopy(detail)
        elif path.startswith('/api/submissions/'+identity+'/log'):
            return 200,b'only this submission\n'
        elif path=='/api/submissions?limit=0':
            return 400,json.dumps({'api_version':'1','error':{'code':'bad-query','detail':'limit','field':'limit'}}).encode()
        else:
            return 404,json.dumps({'api_version':'1','error':{'code':'submission-not-found','detail':'not found'}}).encode()
        return 200,json.dumps(data).encode()
    monkeypatch.setattr(uat,'raw_get',get)
    return uat,detail,receipt


def test_six_submission_checks_reuse_artifacts_and_keep_invalid_warnings(smoke,submitted):
    uat,detail,_=submitted
    checks=[check for check in smoke.CHECK_LIST if check.sid.startswith('14-')]
    assert len(checks)==6
    for check in checks:
        check.fn(uat)
    assert detail['result']['pools']['train']['invalid']==1 and detail['result']['warnings']
    assert uat.reqs and all(method=='GET' for method,_ in uat.reqs)


@pytest.mark.parametrize('change',[{'result':{}},{'run_id':'another-run'},
                                  {'status':'failed'},{'queue_position':1}])
def test_wrong_scored_detail_cannot_pass(smoke,submitted,change):
    uat,detail,_=submitted
    detail.update(change)
    with pytest.raises(smoke.Fail):
        smoke.c14_3(uat)


def test_changed_curve_is_not_hidden_by_a_self_consistent_http_result(smoke,submitted,roots):
    uat,_,receipt=submitted
    curve=roots/'runs'/receipt['run_id']/'curve.jsonl'
    point=json.loads(curve.read_text())
    point['score']=.9
    curve.write_text(json.dumps(point)+'\n')
    with pytest.raises(smoke.Fail):
        smoke.c14_4(uat)


def test_health_rebinding_cannot_choose_submission_artifact_root(smoke,submitted,roots):
    uat,_,_=submitted
    uat.runs_root=roots/'server-selected'
    uat.health['runs_root']=str(roots/'server-selected')
    assert uat.submission_runs_root==roots/'runs'
    with pytest.raises(smoke.Fail,match='root'):
        smoke.submission_artifacts(uat)


def test_disabled_legacy_console_is_blocked_but_required_feature_fails(smoke,roots,monkeypatch):
    uat=smoke.UAT('http://127.0.0.1:4470',roots/'runs',1000,roots/'evidence')
    monkeypatch.setattr(uat,'raw_get',lambda path:(503,b'{"api_version":"1","error":{"code":"service-not-ready","detail":"not configured"}}'))
    with pytest.raises(smoke.Blocked):
        smoke.c14_1(uat)
    uat.submission_required=True
    with pytest.raises(smoke.Fail):
        smoke.c14_1(uat)


def test_non_get_request_is_a_submission_uat_failure(smoke,submitted):
    uat,_,_=submitted
    uat.reqs.append(('POST',uat.url+'/api/submissions'))
    with pytest.raises(smoke.Fail):
        smoke.c14_6(uat)
