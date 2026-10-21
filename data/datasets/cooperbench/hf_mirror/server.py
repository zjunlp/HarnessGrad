"""Read-only pinned Hub mirror. There is deliberately no network client here."""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import urllib.parse

PORT = 18765


def load(root):
    raw = (root / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest['version'] != 1: raise ValueError('unsupported mirror manifest')
    for row in manifest['repos'].values():
        for key in ('info', 'tree'):
            data = (root / 'blobs' / row[key + '_blob']).read_bytes()
            if hashlib.sha256(data).hexdigest() != row[key + '_sha256'] or json.loads(data) != row[key]:
                raise ValueError('metadata identity mismatch')
        for item in row['files'].values():
            path = root / 'blobs' / item['sha256']
            sha = hashlib.sha256()
            with path.open('rb') as source:
                while chunk := source.read(1024 * 1024): sha.update(chunk)
            if sha.hexdigest() != item['sha256'] or path.stat().st_size != item['size']:
                raise ValueError('weight identity mismatch')
    return manifest, hashlib.sha256(raw).hexdigest()


def handler(root, manifest, identity):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_HEAD(self): self.serve(False)
        def do_GET(self): self.serve(True)

        def serve(self, body):
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)
            if path == '/health' and not query:
                return self.reply(200, json.dumps(identity).encode(), body)
            for repo, row in manifest['repos'].items():
                revs = ('main', row['revision'])
                info_paths = [f'/api/models/{repo}', *[f'/api/models/{repo}/revision/{r}' for r in revs]]
                if path in info_paths and not query:
                    return self.reply(200, (root / 'blobs' / row['info_blob']).read_bytes(), body)
                if path in [f'/api/models/{repo}/tree/{r}' for r in revs]:
                    if any(k not in ('recursive', 'expand') or v not in (['true'], ['false'], ['True'], ['False'], ['1'], ['0']) for k, v in query.items()):
                        return self.reply(404, b'{}', body)
                    return self.reply(200, (root / 'blobs' / row['tree_blob']).read_bytes(), body)
                for rev in revs:
                    prefix = f'/{repo}/resolve/{rev}/'
                    if path.startswith(prefix) and not query:
                        item = row['files'].get(path[len(prefix):])
                        if item is None: return self.reply(404, b'{}', body)
                        self.send_response(200)
                        self.send_header('Content-Length', str(item['size']))
                        self.send_header('ETag', '"' + item['etag'] + '"')
                        self.send_header('X-Repo-Commit', item['commit'])
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.end_headers()
                        if body:
                            with (root / 'blobs' / item['sha256']).open('rb') as source:
                                while chunk := source.read(1024 * 1024): self.wfile.write(chunk)
                        return
            self.reply(404, b'{}', body)

        def reply(self, status, raw, body):
            self.send_response(status)
            if status == 404: self.send_header('X-Error-Code', 'EntryNotFound')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            if body: self.wfile.write(raw)
    return Handler


def serve(root, port, identity, public=None):
    manifest, sha = load(root)
    if identity['manifest_sha256'] != sha: raise ValueError('launch manifest mismatch')
    server = ThreadingHTTPServer(('127.0.0.1', port), handler(root, manifest, identity))
    if public is not None:
        pid = os.getpid()
        starttime = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        identity.update(pid=pid, starttime=starttime,
                        nonce_sha256=hashlib.sha256(identity.pop('nonce').encode()).hexdigest())
        fd = os.open(public, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, 'w') as out: json.dump(identity, out)
    server.daemon_threads = True
    server.serve_forever(poll_interval=.2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--public-identity', type=Path)
    args = parser.parse_args()
    serve(args.root, args.port, json.loads(args.identity.read_text()), args.public_identity)
