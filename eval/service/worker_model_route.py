"""One fixed chat-completions HTTP operation over a private worker Unix socket.

Unlike a generic TCP tunnel, requests cannot select another route or protocol.
Streaming bodies are forwarded without buffering the episode or changing budgets.
"""
import http.client
import hashlib
from http.server import BaseHTTPRequestHandler
import os
import math
from pathlib import Path
import shutil
import socketserver
import tempfile
import threading
from urllib.parse import urlsplit


class ModelRoute:
    def __init__(self, base_url, *, timeout_s, max_connections=16,
                 request_max_bytes=16 << 20, response_max_bytes=64 << 20,
                 header_timeout_s=30):
        # Legacy callers retain their previous bounds. Trusted runtime policies
        # can explicitly remove any bound with None; the route remains stoppable.
        for key, value in (('max_connections', max_connections),
                           ('request_max_bytes', request_max_bytes),
                           ('response_max_bytes', response_max_bytes)):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f'{key} must be a positive integer or None')
        for key, value in (('timeout_s', timeout_s), ('header_timeout_s', header_timeout_s)):
            if value is not None and (type(value) not in (int, float)
                                      or not math.isfinite(value) or value <= 0):
                raise ValueError(f'{key} must be positive and finite or None')
        self.max_connections = max_connections
        self.request_max_bytes = request_max_bytes
        self.response_max_bytes = response_max_bytes
        self.header_timeout_s = header_timeout_s
        url = urlsplit(base_url)
        if (url.scheme != 'http' or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError('worker model requires an explicit plain HTTP endpoint')
        self.endpoint_sha256 = hashlib.sha256(base_url.encode()).hexdigest()
        self.target = (url.hostname, url.port or 80)
        self.url_path = url.path.rstrip('/')
        self.operation = self.url_path + '/chat/completions'
        self.timeout_s = timeout_s
        self.faults = []; self.calls = 0; self.connections = set(); self.upstream_sockets = set(); self.clients = set(); self.lock = threading.Lock(); self.stopped = False
        self.directory = None; self.server = None; self.thread = None

    def start(self):
        owner = self
        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            def get_request(self):
                client, address = super().get_request()
                deadlines = [value for value in (owner.header_timeout_s, owner.timeout_s)
                             if value is not None]
                client.settimeout(min(deadlines) if deadlines else None)
                with owner.lock:
                    if owner.stopped or (owner.max_connections is not None
                                         and len(owner.clients) >= owner.max_connections):
                        client.close()
                        owner.faults.append('model connection limit')
                        raise OSError('route closed or full')
                    owner.clients.add(client)
                return client, address
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def finish(self):
                try: super().finish()
                finally:
                    with owner.lock: owner.clients.discard(self.request)
            def do_POST(self):
                if (self.path != owner.operation or self.headers.get('Transfer-Encoding')
                        or len(self.headers.get_all('Content-Length', [])) != 1):
                    owner.faults.append('unauthorized operation or framing')
                    self.send_error(403); return
                try:
                    size = int(self.headers['Content-Length'])
                    if size <= 0: raise ValueError('invalid model request length')
                    if owner.request_max_bytes is not None and size > owner.request_max_bytes:
                        owner.faults.append('model request byte limit')
                        self.send_error(413); return
                    self.connection.settimeout(owner.timeout_s)
                    body = self.rfile.read(size)
                    if len(body) != size: raise ValueError('short model request')
                    conn = http.client.HTTPConnection(*owner.target, timeout=owner.timeout_s)
                    upstream = None
                    reply = None
                    with owner.lock:
                        if owner.stopped: raise OSError('route stopped')
                        owner.calls += 1; owner.connections.add(conn)
                    try:
                        conn.connect()
                        upstream = conn.sock
                        with owner.lock:
                            if owner.stopped: raise OSError('route stopped')
                            owner.upstream_sockets.add(upstream)
                        headers = {'Content-Type': 'application/json', 'Connection': 'close'}
                        if self.headers.get('Authorization'): headers['Authorization'] = self.headers['Authorization']
                        conn.request('POST', owner.operation, body=body, headers=headers)
                        reply = conn.getresponse()
                        if reply.status != 200:
                            owner.faults.append('provider HTTP failure'); self.send_error(502); return
                        self.send_response(200)
                        self.send_header('Content-Type', reply.getheader('Content-Type', 'application/json'))
                        self.send_header('Connection', 'close'); self.end_headers()
                        total = 0
                        while chunk := reply.read1(65536):
                            total += len(chunk)
                            if owner.response_max_bytes is not None and total > owner.response_max_bytes:
                                owner.faults.append('model response byte limit')
                                raise ValueError('model response byte limit')
                            self.wfile.write(chunk); self.wfile.flush()
                        if reply.length not in (None, 0):
                            raise ValueError('short model response')
                    finally:
                        if reply is not None: reply.close()
                        conn.close()
                        with owner.lock:
                            owner.connections.discard(conn)
                            owner.upstream_sockets.discard(upstream)
                except (OSError, ValueError, http.client.HTTPException):
                    owner.faults.append('model transport failure')
                    self.close_connection = True
            def do_GET(self):
                owner.faults.append('unauthorized operation'); self.send_error(403)
            do_CONNECT = do_GET
            do_PUT = do_GET
            do_DELETE = do_GET
            do_HEAD = do_GET
            do_OPTIONS = do_GET
            do_PATCH = do_GET
        try:
            self.directory = Path(tempfile.mkdtemp(prefix='hg-worker-model-'))
            self.path = self.directory / 'model.sock'
            self.server = Server(str(self.path), Handler)
            os.chmod(self.path, 0o666)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
            return self
        except BaseException:
            self.stop(); raise

    def report(self):
        return {'operation': 'POST ' + self.operation, 'endpoint_sha256': self.endpoint_sha256, 'transport': 'unix+isolated-loopback',
                'calls': self.calls, 'faults': list(self.faults),
                'limits': {'connections': self.max_connections, 'request_bytes': self.request_max_bytes,
                           'response_bytes': self.response_max_bytes, 'header_timeout_s': self.header_timeout_s,
                           'timeout_s': self.timeout_s},
                'cleanup': {'complete': self.stopped and not self.connections and not self.clients}}

    def stop(self):
        with self.lock:
            self.stopped = True
            for client in self.clients:
                try: client.shutdown(2)
                except OSError: pass
                client.close()
            # HTTPConnection may detach its socket after Connection: close
            # response headers. Keep the underlying socket cancellable while the
            # handler is still reading the body, including timeout_s=None.
            for upstream in self.upstream_sockets:
                try: upstream.shutdown(2)
                except OSError: pass
                upstream.close()
            for conn in self.connections:
                conn.close()
        if self.thread:
            self.server.shutdown(); self.thread.join(timeout=5)
        if self.server: self.server.server_close()
        if self.directory:
            shutil.rmtree(self.directory)
            self.directory = None
        # Closing sockets wakes all handlers; a short bounded join records lag.
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.lock:
                if not self.connections and not self.clients: break
            time.sleep(.01)
