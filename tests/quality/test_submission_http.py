"""New submission routes have bounded input and preserve the existing console."""
from email.message import Message
import importlib.util
import http.client
import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def server():
    spec = importlib.util.spec_from_file_location('submission_http_server', ROOT/'console/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def headers(**values):
    result = Message()
    for name, value in values.items():
        result[name.replace('_', '-')] = str(value)
    return result


def form(parts=None):
    parts = [('archive', b'binary\x00upload'), ('scenario', b'demo-smoke')] if parts is None else parts
    body = b''
    for name, value in parts:
        body += (f'--BOUNDARY\r\nContent-Disposition: form-data; name="{name}"'
                 + ('; filename="../untrusted.tar.gz"' if name == 'archive' else '')
                 + '\r\n\r\n').encode() + value + b'\r\n'
    body += b'--BOUNDARY--\r\n'
    return headers(Content_Type='multipart/form-data; boundary=BOUNDARY', Content_Length=len(body)), body


def rejected(server, name, *args, **kwargs):
    fn = getattr(server, name, None)
    assert callable(fn), f'A5 {name} is missing'
    with pytest.raises(ValueError) as caught:
        fn(*args, **kwargs)
    assert caught.value.problems[0]['detail']
    return caught.value.problems[0]


@pytest.fixture
def httpd(server, tmp_path, monkeypatch):
    for name in ('submissions','runs','budget'):
        (tmp_path/name).mkdir()
    monkeypatch.setenv('HG_CONSOLE_SUBMISSIONS_ROOT',str(tmp_path/'submissions'))
    monkeypatch.setenv('HG_CONSOLE_SUBMISSIONS_WORK',str(tmp_path/'work'))
    monkeypatch.setattr(server,'RUNS',tmp_path/'runs')
    monkeypatch.setattr(server,'PLATFORM',ROOT)
    monkeypatch.setattr(server,'BUDGET_DIR',tmp_path/'budget')
    monkeypatch.setattr(server,'api_health',lambda:{'original':'health-shape'})
    monkeypatch.setattr(server,'launch_form',lambda:{'original':'launch-form'})
    instance = server.ConsoleServer(('127.0.0.1',0),server.Handler)
    thread = threading.Thread(target=instance.serve_forever,daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join()


def request(httpd, method, route, *, body=None, extra=None):
    client = http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=10)
    client.request(method,route,body=body,headers=extra or {})
    response = client.getresponse()
    raw = response.read()
    result = (response.status,response.getheader('Content-Type'),raw)
    client.close()
    return result


@pytest.mark.parametrize('extra,code,want',[({'Origin':'http://evil.example'},403,'origin-forbidden'),
                                          ({'Host':'evil.example:4460'},400,'bad-host')])
def test_http_delete_has_same_source_guard(httpd,extra,code,want):
    status,_,raw=request(httpd,'DELETE','/api/submissions/sub-20261030T010000Z-1234abcd',extra=extra)
    assert status==code and json.loads(raw)['error']['code']==want


def test_http_existing_routes_keep_original_shapes(httpd):
    for route, expected in [('/api/health',{'original':'health-shape'}),
                            ('/api/launch/form',{'original':'launch-form'})]:
        code,_,raw = request(httpd,'GET',route)
        assert code == 200 and json.loads(raw) == expected


@pytest.mark.parametrize('route,code,want', [('/api/submissions',200,None),
    ('/api/submissions/../escape',404,'submission-not-found'),
    ('/api/submissions/sub-20261030T010000Z-1234abcd',404,'submission-not-found'),
    ('/api/submissions?limit=0',400,'bad-query'),
    ('/api/submissions?limit=',400,'bad-query'),
    ('/api/submissions?cursor=sub-20261030T010000Z-1234abcd',400,'bad-query')])
def test_http_new_get_error_shapes(httpd, route, code, want):
    status,ctype,raw = request(httpd,'GET',route)
    assert status == code and ctype.startswith('application/json')
    value = json.loads(raw)
    assert value['api_version'] == '1'
    if want:
        assert value['error']['code'] == want and value['error']['detail']
    else:
        assert value == {'api_version':'1','items':[],'next_cursor':None}


def test_http_post_guards_before_reading_or_creating_files(httpd, tmp_path):
    h,body = form()
    for extra,code,want in [({'Host':'evil.example:4460'},400,'bad-host'),
                           ({'Origin':'http://evil.example'},403,'origin-forbidden')]:
        status,_,raw = request(httpd,'POST','/api/submissions',body=body,
                               extra={**dict(h.items()),**extra})
        assert status == code and json.loads(raw)['error']['code'] == want
    assert list((tmp_path/'submissions').iterdir()) == []


def test_http_bad_archive_reuses_gate_and_retains_rejected_receipt(httpd, server, tmp_path, monkeypatch):
    from console import submissions
    monkeypatch.setattr(submissions,'scenario_status',lambda *a,**kw:{'available':True,'reason':'ok'})
    h,body = form()
    status,_,raw = request(httpd,'POST','/api/submissions',body=body,extra=dict(h.items()))
    assert status == 400 and json.loads(raw)['error']['code'] == 'bad-archive'
    status,_,raw = request(httpd,'GET','/api/submissions')
    row = json.loads(raw)['items'][0]
    assert row['status'] == 'rejected' and row['score'] is None
    status,_,raw = request(httpd,'GET','/api/submissions/'+row['submission_id'])
    detail = json.loads(raw)
    assert status == 200 and 'result' not in detail and detail['error']['code'] == 'bad-archive'
    assert not list((tmp_path/'submissions').rglob('upload.bin'))


@pytest.mark.parametrize('mode,want', [('missing','bad-multipart'),('duplicate','bad-multipart'),
                                      ('transfer','bad-multipart'),('oversize','payload-too-large')])
def test_http_framing_is_bounded_before_body(server, mode, want):
    h = headers(Content_Length='1')
    if mode == 'missing':
        del h['Content-Length']
    elif mode == 'duplicate':
        h['Content-Length']='1'
    elif mode == 'transfer':
        h['Transfer-Encoding']='chunked'
    else:
        h.replace_header('Content-Length',str(20*1024*1024+65537))
    assert rejected(server,'submission_length',h)['code'] == want


def test_http_invalid_warning_and_log_are_protocol_fixture_controls(httpd, server, tmp_path):
    # Artificial artifacts exercise transport only; they are not benchmark results.
    from tests.quality.test_submission_runtime import artifacts
    from console import submissions
    import shutil
    run,receipt,_ = artifacts(tmp_path,invalid=True)
    runtime = server.submission_service(httpd)
    identity=receipt['submission_id']
    shutil.copytree(run,runtime.runs/identity)
    measured=submissions.read_submission_result(runtime.runs/identity,receipt=receipt)
    directory=runtime.root/identity
    directory.mkdir()
    data={**receipt,'status':'scored','result':measured,'created_at':'2026-10-30T01:00:00Z',
          'label':'fixture','harness':{'name':'fixture','version':'1','entrypoint':'agent.py','env_kinds':['files']}}
    runtime._save(data)
    (directory/'driver.log').write_bytes(b'first\nlast\n')
    code,_,raw=request(httpd,'GET','/api/submissions/'+identity)
    value=json.loads(raw)
    assert code==200 and value['result']['pools']['train']['invalid']==1 and value['result']['warnings']
    code,ctype,raw=request(httpd,'GET',f'/api/submissions/{identity}/log?tail=5')
    assert code==200 and ctype.startswith('text/plain') and raw==b'last\n'
    runtime._update(identity,status='failed')
    assert 'result' not in json.loads(request(httpd,'GET','/api/submissions/'+identity)[2])
    runtime._update(identity,status='validating')
    assert request(httpd,'GET',f'/api/submissions/{identity}/log')[0]==409


def test_http_existing_post_still_uses_original_launch_response(httpd, server, monkeypatch):
    monkeypatch.setattr(server,'api_launch',lambda value:(202,{'run_id':'legacy','form':value}))
    code,_,raw=request(httpd,'POST','/api/runs',body=b'{"dataset":"fixture"}',extra={'Content-Type':'application/json'})
    assert code==202 and json.loads(raw)=={'run_id':'legacy','form':{'dataset':'fixture'}}
