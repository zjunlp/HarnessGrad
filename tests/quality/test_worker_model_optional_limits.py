"""Local HTTP/Unix tests for opt-in worker model transport resource policies."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time

import pytest

from eval.service.worker_model_route import ModelRoute


@pytest.fixture
def provider():
    state = {'requests': [], 'response_bytes': 2, 'entered': threading.Event(),
             'release': threading.Event(), 'stall_body': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            size = int(self.headers['Content-Length'])
            remaining = size
            while remaining:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
            state['requests'].append((self.path, size))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Connection', 'close')
            self.send_header('Content-Length', str(state['response_bytes']))
            self.end_headers()
            state['entered'].set()
            if state['stall_body']:
                state['release'].wait(10)
            remaining = state['response_bytes']
            try:
                while remaining:
                    n = min(65536, remaining)
                    self.wfile.write(b'x' * n)
                    remaining -= n
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state['url'] = f'http://127.0.0.1:{server.server_port}/v1'
    yield state
    state['release'].set()
    server.shutdown()
    server.server_close()
    thread.join()


def connect(route):
    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect(str(route.path))
    return UnixConnection('localhost')


def uncapped(provider, **kwargs):
    params = dict(timeout_s=None, max_connections=None, request_max_bytes=None,
                  response_max_bytes=None, header_timeout_s=None)
    return ModelRoute(provider['url'], **{**params, **kwargs}).start()


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_explicit_none_exceeds_old_request_and_response_caps(provider):
    provider['response_bytes'] = (64 << 20) + 1
    route = uncapped(provider)
    connection = connect(route)
    try:
        connection.request('POST', '/v1/chat/completions', body=b'x' * ((16 << 20) + 1))
        reply = connection.getresponse()
        assert reply.status == 200
        count = 0
        while chunk := reply.read(65536):
            count += len(chunk)
        assert count == provider['response_bytes']
        assert provider['requests'] == [('/v1/chat/completions', (16 << 20) + 1)]
        assert not route.faults
    finally:
        connection.close()
        route.stop()
    assert all(value is None for value in route.report()['limits'].values())
    assert route.report()['cleanup']['complete']


def test_optional_connections_and_idle_header_deadline(provider):
    route = uncapped(provider)
    clients = []
    try:
        for _ in range(17):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(route.path))
            clients.append(client)
        wait_for(lambda: len(route.clients) == 17)
        assert all(client.gettimeout() is None for client in route.clients)
        assert not route.faults
    finally:
        route.stop()
        for client in clients:
            client.close()
    assert route.report()['cleanup']['complete']


def test_explicit_connection_limit_still_enforced(provider):
    route = uncapped(provider, max_connections=1)
    clients = []
    try:
        for _ in range(2):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2)
            client.connect(str(route.path))
            clients.append(client)
        wait_for(lambda: bool(route.faults))
        assert route.faults == ['model connection limit']
        assert clients[1].recv(1) == b''
    finally:
        route.stop()
        for client in clients:
            client.close()


def test_explicit_request_cap_refuses_before_provider(provider):
    route = uncapped(provider, request_max_bytes=1)
    connection = connect(route)
    try:
        connection.request('POST', '/v1/chat/completions', body='{}')
        reply = connection.getresponse()
        assert reply.status == 413
        reply.read()
        assert not provider['requests']
        assert 'model request byte limit' in route.faults
    finally:
        connection.close()
        route.stop()


def test_explicit_response_cap_reports_incomplete_transport(provider):
    provider['response_bytes'] = 128
    route = uncapped(provider, response_max_bytes=64)
    connection = connect(route)
    try:
        connection.request('POST', '/v1/chat/completions', body='{}')
        reply = connection.getresponse()
        assert len(reply.read()) < 128
        assert 'model response byte limit' in route.faults
    finally:
        connection.close()
        route.stop()
    assert route.report()['cleanup']['complete']


def test_stop_interrupts_unbounded_detached_response_socket(provider):
    provider['stall_body'] = True
    route = uncapped(provider)
    connection = connect(route)
    try:
        connection.request('POST', '/v1/chat/completions', body='{}')
        reply = connection.getresponse()
        assert provider['entered'].wait(5)
        # Connection: close detaches HTTPConnection.sock before read1 blocks.
        assert any(conn.sock is None for conn in route.connections)
        route.stop()
        assert reply.read() == b''
        assert 'model transport failure' in route.faults
        assert route.report()['cleanup']['complete']
    finally:
        provider['release'].set()
        connection.close()
        route.stop()


def test_legacy_defaults_remain_visible(provider):
    route = ModelRoute(provider['url'], timeout_s=60).start()
    try:
        assert route.report()['limits'] == {'connections': 16, 'request_bytes': 16 << 20,
            'response_bytes': 64 << 20, 'timeout_s': 60, 'header_timeout_s': 30}
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(route.path))
            wait_for(lambda: len(route.clients) == 1)
            assert next(iter(route.clients)).gettimeout() == 30
        finally:
            client.close()
    finally:
        route.stop()


@pytest.mark.parametrize('field,value', [('max_connections', 0), ('request_max_bytes', False),
    ('response_max_bytes', -1), ('header_timeout_s', float('inf')), ('timeout_s', -1)])
def test_invalid_resource_policies_fail_before_listening(provider, field, value):
    with pytest.raises(ValueError):
        uncapped(provider, **{field: value})


@pytest.mark.parametrize('method,path', [('GET', '/v1/models'), ('POST', '/v1/embeddings'),
                                        ('CONNECT', 'outside.invalid:443')])
def test_removing_caps_preserves_endpoint_operation_binding(provider, method, path):
    route = uncapped(provider)
    connection = connect(route)
    try:
        connection.request(method, path, body='{}')
        reply = connection.getresponse()
        assert reply.status == 403
        reply.read()
        assert not provider['requests']
        assert route.faults
    finally:
        connection.close()
        route.stop()
