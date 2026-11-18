"""Owned loopback HTTP service; no experiment routes or model execution."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

from harnessgrad import PLATFORM_ROOT
from harnessgrad.personal.contracts import MAX_BYTES, decode, encode
from harnessgrad.personal.fixtures import example
from harnessgrad.personal.event_store import DraftConflict
from harnessgrad.personal.service import PersonalService

ASSETS = {'/': ('personal.html', 'text/html'), '/personal.js': ('personal.js', 'text/javascript'),
          '/personal-window.js': ('personal-window.js', 'text/javascript'),
          '/personal-draft.js': ('personal-draft.js', 'text/javascript'),
          '/personal.css': ('personal.css', 'text/css'), '/tokens.css': ('tokens.css', 'text/css')}
ASSET_ROOT = PLATFORM_ROOT / 'console' / 'static'


class PersonalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, service: PersonalService):
        self.service = service
        self.instance = secrets.token_hex(16)
        self.token = secrets.token_hex(32)
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server_port}/'
        self.origin = self.url.rstrip('/')

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server: PersonalServer

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass  # No paths, source bodies, connection tokens or request headers in logs.

    def reply(self, status, value, *, content_type='application/json'):
        body = encode(value).encode('utf-8') if content_type == 'application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self' ipc: http://ipc.localhost; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, *, mutation=False):
        self.connection.settimeout(5)
        try:
            if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
                self.reply(403, {'error': 'unexpected local host'})
                return
            origin = self.headers.get('Origin')
            if origin is not None and origin != self.server.origin:
                self.reply(403, {'error': 'unexpected request origin'})
                return
            route = urlsplit(self.path)
            if not mutation and route.path in ASSETS and not route.query:
                filename, kind = ASSETS[route.path]
                data = (ASSET_ROOT / filename).read_bytes()
                self.reply(200, data, content_type=kind)
                return
            supplied = self.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied, 'Bearer ' + self.server.token):
                self.reply(401, {'error': 'local connection token required'})
                return
            params = parse_qs(route.query, strict_parsing=True)
            if any(len(v) != 1 for v in params.values()):
                raise ValueError('repeated query parameter')
            if mutation:
                if self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('expected application/json')
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('transfer encoding is not supported')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BYTES:
                    raise ValueError('request size outside limit')
                value = decode(self.rfile.read(length))
                result = self.post(route.path, value)
            else:
                result = self.get(route.path, params)
            self.reply(200, result)
        except DraftConflict:
            self.reply(409, {'error': 'draft changed in another editor; retain local text and reload'})
        except (ValueError, KeyError, TypeError):
            self.reply(400, {'error': 'invalid personal request; check selection, schema and limits'})
        except (OSError, RuntimeError, sqlite3.Error):
            self.reply(503, {'error': 'personal operation unavailable; no harness was changed'})

    def get(self, route, params):
        service = self.server.service
        if route == '/api/personal/health':
            return {**service.snapshot(), 'instance': self.server.instance, 'schema_version': 1}
        if route == '/api/personal/example':
            return example()
        if route == '/api/personal/projects':
            return {'projects': service.store.projects()}
        parts = route.strip('/').split('/')
        if len(parts) == 5 and parts[:3] == ['api', 'personal', 'projects']:
            if parts[4] == 'draft' and not params:
                return service.store.draft(parts[3])
            if parts[4] == 'sessions' and not params:
                return {'sessions': service.store.sessions(parts[3])}
            if parts[4] == 'events' and not set(params) - {'after', 'limit'}:
                return service.store.source_rows(parts[3], after=int(params.get('after', ['0'])[0]),
                                                 limit=int(params.get('limit', ['50'])[0]))
        if len(parts) == 5 and parts[:3] == ['api', 'personal', 'jobs'] and parts[4] == 'events':
            return {'events': service.store.job_events(parts[3], int(params.get('after', ['0'])[0]))}
        raise ValueError('unknown personal route')

    def post(self, route, value):
        service = self.server.service
        if route == '/api/personal/draft' and set(value) == {'project_id', 'text', 'revision'}:
            return service.store.save_draft(value['project_id'], value['text'], value['revision'])
        if route == '/api/personal/projects' and set(value) == {'root'}:
            return service.add_project(Path(value['root']))
        if route == '/api/personal/import' and set(value) == {'project_id', 'document'}:
            return service.import_session(value['project_id'], value['document'])
        if route == '/api/personal/import' and set(value) == {'project_id', 'raw_document'}:
            return service.import_session(value['project_id'], decode(value['raw_document']))
        if route == '/api/personal/probe' and not value:
            return service.probe()
        if route == '/api/personal/demo' and set(value) == {'project_id', 'state'}:
            return service.store.demo(value['project_id'], value['state'])
        if route == '/api/personal/shutdown' and value == {'instance': self.server.instance}:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return {'instance': self.server.instance, 'status': 'stopping'}
        raise ValueError('unknown or invalid personal operation')

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(mutation=True)


def serve(service: PersonalService, *, ready=print):
    server = PersonalServer(service)
    connection = service.root / 'connection.json'
    descriptor = {'schema': 'harnessgrad.personal.connection.v1', 'url': server.url,
                  'token': server.token, 'instance': server.instance, 'pid': os.getpid()}
    owned = False
    try:
        with connection.open('x', encoding='utf-8') as handle:
            owned = True
            os.chmod(connection, 0o600)
            handle.write(encode(descriptor))
        ready(encode({'status': 'serving', 'url': server.url, 'connection_file': str(connection)}))
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        if owned and connection.is_file():
            current = decode(connection.read_bytes())
            if current.get('instance') == server.instance:
                connection.unlink()
