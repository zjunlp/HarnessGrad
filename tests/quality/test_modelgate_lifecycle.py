"""Real owned listeners and standard Thread faults; no upstream/model or Docker."""
import json
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import threading

import pytest
from eval.service import modelgate

ROOT=Path(__file__).resolve().parents[2]


def reachable(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=0.2):return True
    except OSError:return False


@pytest.mark.parametrize('failure',['RuntimeError','KeyboardInterrupt'])
def test_never_started_thread_preserves_error_and_closes_listener(tmp_path,failure):
    marker=tmp_path/'state.json'
    code='''import json,sys
from pathlib import Path
sys.path.insert(0,ROOT)
from eval.service import modelgate
state=Path(MARKER);gateway=modelgate.ModelGateway(bind_host='127.0.0.1',target=('127.0.0.1',9))
failure=FAILURE('authored thread start fault')
def start(self):raise failure
modelgate.threading.Thread.start=start
try:gateway.start()
except BaseException as error:
 state.write_text(json.dumps({'phase':'start-refused','same_error':error is failure,'port':gateway.port}))
 gateway.stop();gateway.stop()
 state.write_text(json.dumps({'phase':'closed','same_error':error is failure,'port':gateway.port,
  'server_none':gateway._server is None,'thread_none':gateway._thread is None}))
else:raise AssertionError('start fault was lost')
'''.replace('ROOT',repr(str(ROOT))).replace('MARKER',repr(str(marker))).replace('FAILURE',failure)
    process=subprocess.Popen([sys.executable,'-I','-B','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    timed_out=False
    try:stdout,stderr=process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        timed_out=True
        state=json.loads(marker.read_text())
        leaked=reachable(state['port'])
        process.kill();stdout,stderr=process.communicate(timeout=3)
    else:
        state=json.loads(marker.read_text());leaked=reachable(state['port'])
    assert not timed_out, {'state':state,'listener_reachable':leaked}
    assert process.returncode==0,stderr
    assert state['phase']=='closed' and state['same_error'] and state['server_none'] and state['thread_none']
    assert not leaked


def test_stop_without_start_and_after_finished_thread_is_idempotent():
    gateway=modelgate.ModelGateway(bind_host='127.0.0.1',target=('127.0.0.1',9))
    gateway.stop();gateway.stop()
    gateway.start();server,thread=gateway._server,gateway._thread;port=gateway.port
    server.shutdown();thread.join(timeout=2)
    assert not thread.is_alive()
    gateway.stop();gateway.stop()
    assert gateway._server is gateway._thread is None and not reachable(port)


@pytest.mark.parametrize('failure',[RuntimeError('started fault'),KeyboardInterrupt('started cancel')])
def test_start_fault_after_real_thread_started_closes_only_owned_listener(monkeypatch,failure):
    gateway=modelgate.ModelGateway(bind_host='127.0.0.1',target=('127.0.0.1',9))
    original=threading.Thread.start
    def start(thread):
        original(thread)
        raise failure
    monkeypatch.setattr(threading.Thread,'start',start)
    with pytest.raises(type(failure)) as raised:gateway.start()
    assert raised.value is failure
    gateway.stop();gateway.stop()
    assert gateway._server is gateway._thread is None and not reachable(gateway.port)


def test_concurrent_stop_waits_for_the_start_state_handoff(monkeypatch):
    gateway=modelgate.ModelGateway(bind_host='127.0.0.1',target=('127.0.0.1',9))
    entered,release=threading.Event(),threading.Event();original=threading.Thread.start;errors=[]
    def start(thread):
        if getattr(getattr(thread,'_target',None),'__func__',None) is modelgate.ModelGateway._serve:
            entered.set();assert release.wait(2)
        return original(thread)
    monkeypatch.setattr(threading.Thread,'start',start)
    def open_gateway():
        try:gateway.start()
        except BaseException as error:errors.append(error)
    starter=threading.Thread(target=open_gateway);starter.start();assert entered.wait(2)
    stopper=threading.Thread(target=gateway.stop);stopper.start();release.set()
    starter.join(3);stopper.join(3)
    assert not starter.is_alive() and not stopper.is_alive() and not errors
    gateway.stop()
    assert gateway._server is gateway._thread is None and not reachable(gateway.port)


def test_real_forwarding_and_accounting_survive_stop():
    payload=b'authored-local-payload'*200
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            parts=[]
            while True:
                block=self.request.recv(65536)
                if not block:break
                parts.append(block)
            self.request.sendall(b'echo:'+b''.join(parts))
    upstream=socketserver.TCPServer(('127.0.0.1',0),Echo)
    thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    gateway=modelgate.ModelGateway(bind_host='127.0.0.1',target=upstream.server_address,path='/v1')
    try:
        gateway.start();port=gateway.port
        assert gateway.base_url.endswith('/v1')
        with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
            client.sendall(payload);client.shutdown(socket.SHUT_WR);pieces=[]
            while True:
                block=client.recv(65536)
                if not block:break
                pieces.append(block)
        assert b''.join(pieces)==b'echo:'+payload
        gateway.stop();gateway.stop()
        account=gateway.account()
        assert account['connections']==1 and account['upstream_connect_failed']==0
        assert account['bytes_to_upstream']==len(payload)
        assert account['bytes_to_client']==len(payload)+5
        assert gateway._server is gateway._thread is None and not reachable(port)
    finally:
        gateway.stop();upstream.shutdown();upstream.server_close();thread.join(2)


def test_stop_between_thread_bootstrap_and_real_serve_loop(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    original=modelgate._Server.serve_forever
    def serve(server):
        entered.set();assert release.wait(2)
        original(server)
    monkeypatch.setattr(modelgate._Server,'serve_forever',serve)
    gateway=modelgate.ModelGateway(bind_host='127.0.0.1',target=('127.0.0.1',9)).start()
    assert entered.wait(2)
    stopper=threading.Thread(target=gateway.stop);stopper.start();release.set();stopper.join(3)
    assert not stopper.is_alive()
    gateway.stop()
    assert gateway._server is gateway._thread is None and not reachable(gateway.port)


def test_rejected_double_start_preserves_the_existing_owner():
    gateway=modelgate.ModelGateway(bind_host='127.0.0.1',target=('127.0.0.1',9)).start()
    server,thread=gateway._server,gateway._thread
    try:
        with pytest.raises(modelgate.GatewayUnavailable):gateway.start()
        assert gateway._server is server and gateway._thread is thread
        assert server.socket.fileno()>=0 and thread.is_alive()
    finally:gateway.stop()
    assert not reachable(gateway.port)
