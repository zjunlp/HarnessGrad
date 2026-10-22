"""One immutable public PDF over local TLS; never connects to any origin."""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import ssl
import socket
import threading

URL = 'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'
HOST = 'www.w3.org'
RESOURCE = '/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'
PDF_SHA256 = '3df79d34abbca99308e79cb94461c1893582604d68329a41fd4bec1885e6adb4'
PDF_SIZE = 13264


def load(root):
    raw = (root / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest.get('version') != 1 or manifest.get('url') != URL:
        raise ValueError('unexpected fixed HTTPS asset manifest')
    expected = manifest['files']
    if set(expected) != {'dummy.pdf', 'ca.pem', 'server.pem', 'server.key'}:
        raise ValueError('unexpected asset files')
    if expected['dummy.pdf'] != {'sha256': PDF_SHA256, 'size': PDF_SIZE}:
        raise ValueError('public PDF identity changed')
    for name, item in expected.items():
        data = (root / name).read_bytes()
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('asset identity mismatch: ' + name)
    return manifest, hashlib.sha256(raw).hexdigest()


class BoundedHeaders:
    def __init__(self, stream): self.stream, self.remaining = stream, 65536
    def readline(self, size=-1):
        limit = self.remaining if size < 0 else min(size, self.remaining)
        data = self.stream.readline(limit + 1)
        self.remaining -= len(data)
        if self.remaining < 0: raise http.client.LineTooLong('request headers exceed 64 KiB')
        return data
    def close(self): self.stream.close()


class BoundedRequest(BaseHTTPRequestHandler):
    def setup(self):
        super().setup(); self.connection.settimeout(5)
        self.rfile = BoundedHeaders(self.rfile)
        def expire():
            try: self.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self.deadline = threading.Timer(5, expire)
        self.deadline.daemon = True; self.deadline.start()
    def finish(self):
        self.deadline.cancel(); super().finish()


def handler(root, identity):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(root / 'server.pem', root / 'server.key')
    def sni(socket, name, _context):
        if name != HOST:
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
    context.set_servername_callback(sni)

    class FixedAsset(BoundedRequest):
        def log_message(self, *args): pass
        def do_GET(self): self.serve(True)
        def do_HEAD(self): self.serve(False)
        def serve(self, body):
            if (self.path != RESOURCE or self.headers.get_all('Host') not in ([HOST], [HOST + ':443'])
                    or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0'):
                self.send_error(404); return
            data = (root / 'dummy.pdf').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if body: self.wfile.write(data)

    class Proxy(BoundedRequest):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path != '/health': self.send_error(404); return
            data = json.dumps(identity).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Content-Type', 'application/json')
            self.end_headers(); self.wfile.write(data)
        def do_CONNECT(self):
            if (self.path != HOST + ':443' or self.headers.get_all('Host') not in (None, [HOST + ':443'])
                    or self.headers.get('Transfer-Encoding')
                    or self.headers.get('Content-Length', '0') != '0'):
                self.send_error(403); return
            self.send_response(200, 'Connection established'); self.end_headers(); self.wfile.flush()
            self.close_connection = True
            try:
                with context.wrap_socket(self.connection, server_side=True) as tunnel:
                    self.connection = tunnel
                    FixedAsset(tunnel, self.client_address, self.server)
            except (OSError, ssl.SSLError):
                pass
    return Proxy


def serve(root, port, identity, public=None):
    _, sha = load(root)
    if identity['manifest_sha256'] != sha: raise ValueError('launch manifest mismatch')
    server = ThreadingHTTPServer(('127.0.0.1', port), handler(root, identity))
    if public is not None:
        pid = os.getpid()
        start = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        identity.update(pid=pid, starttime=start,
                        nonce_sha256=hashlib.sha256(identity.pop('nonce').encode()).hexdigest())
        fd = os.open(public, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, 'w') as out: json.dump(identity, out)
    server.daemon_threads = True
    server.serve_forever(poll_interval=.2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('--public-identity', type=Path)
    args = parser.parse_args()
    serve(args.root, args.port, json.loads(args.identity.read_text()), args.public_identity)
