"""Shared callback and opt-in deadline controls, using only a scripted loopback."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harnessgrad.improvement.model import JsonModel
from harnessgrad.personal.fixtures import ScriptedModelClient
from methods.endpoint_client import Client, EndpointError, validate_config

KEY = 'scripted-transport-credential'


@pytest.fixture
def endpoint(monkeypatch):
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.delenv(key, raising=False)
    requests, plan = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            status, body, headers, slow = plan.pop(0)
            self.send_response(status)
            self.send_header('Content-Length',str(len(body)))
            for name,value in headers.items():self.send_header(name,value)
            self.end_headers()
            try:
                if slow:
                    self.wfile.write(body[:1]);self.wfile.flush();time.sleep(.25)
                    self.wfile.write(body[1:])
                else:self.wfile.write(body)
            except (OSError, ConnectionError): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}/v1',requests,plan
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def response(content='{}', usage=None):
    return json.dumps({'choices':[{'message':{'content':content}}],'usage':usage}).encode()


@pytest.mark.parametrize('content',['[]','{"x":1,"x":2}','{"x":NaN}','not JSON'])
def test_shared_callback_rejects_invalid_json_without_echoing(content):
    client=ScriptedModelClient([content])
    callback=JsonModel(base_url='http://127.0.0.1:1/v1',api_key=KEY,model='fixture',
                       max_calls=1,system_prompt='Only supplied evidence.',client=client)
    with pytest.raises(ValueError,match='one JSON object') as exc:callback('selected input')
    assert content not in str(exc.value)
    assert callback.usage()['logical_calls'] == 1 and len(callback.usage()['calls']) == 1
    assert client.requests[0]['messages'][0]['content'] == 'Only supplied evidence.'


def test_expired_deadline_does_not_send_a_request(endpoint):
    url,requests,_=endpoint
    client=Client(base_url=url,api_key=KEY)
    with pytest.raises(EndpointError,match='deadline exceeded') as exc:
        client.complete(model='fixture',temperature=0,messages=[],deadline=time.monotonic()-1)
    assert exc.value.attempts == 0 and not requests


def test_retry_after_cannot_extend_the_call_deadline(endpoint):
    url,requests,plan=endpoint
    plan.append((429,b'private response',{'Retry-After':'1'},False))
    client=Client(base_url=url,api_key=KEY,timeout=180)
    with pytest.raises(EndpointError,match='deadline prevents retry') as exc:
        client.complete(model='fixture',temperature=0,messages=[],deadline=time.monotonic()+.3)
    assert exc.value.attempts == 1 and len(requests) == 1
    assert 'private response' not in str(exc.value)


def test_slow_body_is_bounded_by_global_deadline(endpoint):
    url,requests,plan=endpoint
    plan.append((200,response(),{},True))
    client=Client(base_url=url,api_key=KEY,timeout=180)
    started=time.monotonic()
    with pytest.raises(EndpointError) as exc:
        client.complete(model='fixture',temperature=0,messages=[],deadline=started+.08)
    assert exc.value.attempts == 1 and len(requests) == 1
    assert time.monotonic()-started < 1


def test_retry_usage_and_failures_remain_unknown_in_personal_ledger(endpoint,tmp_path,monkeypatch):
    from harnessgrad.personal.model import ModelSession
    from harnessgrad.personal.profile import load_model_settings
    from harnessgrad.personal.service import PersonalService
    import methods.endpoint_client as transport
    monkeypatch.setattr(transport.time,'sleep',lambda _:None)
    url,requests,plan=endpoint
    plan.extend([(500,b'private failure',{},False),
                 (200,response('{"decision":"no_change"}',{'prompt_tokens':5,'completion_tokens':2}),{},False),
                 *[(503,b'private failure',{},False)]*3])
    project=tmp_path/'project';project.mkdir();service=PersonalService(tmp_path/'state')
    config=load_model_settings({'HG_METHOD_BASE_URL':url,'HG_METHOD_API_KEY':KEY,'HG_METHOD_MODEL':'fixture'})
    session=ModelSession(service.store,service.add_project(project)['id'],config,fixture=True)
    with session:
        assert session('first') == {'decision':'no_change'}
        with pytest.raises(EndpointError):session('second')
    usage=session.usage()
    assert usage['logical_calls'] == 2 and usage['transport_attempts'] == 5
    assert usage['unknown_transport_attempts'] == 4 and usage['prompt_tokens'] is None
    assert usage['calls'][0]['prompt_tokens'] == 5 and usage['calls'][1]['prompt_tokens'] is None
    assert len(requests) == 5
    with service.store.connection() as db:dump='\n'.join(db.iterdump())
    assert KEY not in dump and 'private failure' not in dump


def test_validate_config_keeps_constructor_error_messages():
    with pytest.raises(EndpointError,match='invalid method endpoint URL'):
        validate_config(base_url='http://user:private@localhost',api_key=KEY)
    with pytest.raises(EndpointError,match='timeout must be finite and positive'):
        validate_config(base_url='http://127.0.0.1:1/v1',api_key=KEY,timeout=False)
