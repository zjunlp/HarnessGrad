"""Self-authored HTTP acquisition controls; no released pool or scorer dependency."""
import hashlib
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
import urllib.parse

import pytest

ROOT = Path(__file__).resolve().parents[2]
BODY = b'{"authored":"snapshot control"}\n'


@pytest.fixture
def acquisition(tmp_path, monkeypatch):
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy', '127.0.0.1,localhost')
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_DATA_REASONING_GYM', str(tmp_path/'published'))
    spec = importlib.util.spec_from_file_location('rg_url_control', ROOT/'data/datasets/reasoning_gym/prepare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'POOL_SHA256', hashlib.sha256(BODY).hexdigest())
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            seen.append(self.path)
            route = urllib.parse.urlsplit(self.path).path
            if route.startswith('/slow-headers'):
                time.sleep(2)
                return
            if route.startswith('/redirect'):
                self.send_response(302)
                self.send_header('Location', '/good?secret=redirect-token')
                self.end_headers()
                return
            if route.startswith('/error'):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b'private server error')
                return
            self.send_response(200)
            if route.startswith('/huge'):
                self.send_header('Content-Length', str(module.SNAPSHOT_MAX_BYTES+1))
            elif route.startswith('/interrupt'):
                self.send_header('Content-Length', str(len(BODY)+1))
            elif route.startswith('/badlength'):
                self.send_header('Content-Length', 'secret-length-token')
            self.end_headers()
            self.wfile.write(b'wrong bytes' if route.startswith('/wrong') else BODY)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield module, f'http://127.0.0.1:{server.server_port}', seen
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_fetch_checked_bytes_and_sanitized_receipt(acquisition, capsys):
    module, origin, seen = acquisition
    assert module.main(['--snapshot-url', origin+'/good?secret=private-token']) == 0
    assert module.POOL.read_bytes() == BODY
    receipt = json.loads((module.DATASET_ROOT/'pool.snapshot.json').read_text())
    assert receipt['source_origin'] == origin
    assert receipt['acquisition'] == 'operator-supplied-url-snapshot'
    assert 'private-token' not in json.dumps(receipt)+capsys.readouterr().out
    before = {p.name:p.read_bytes() for p in module.DATASET_ROOT.iterdir()}
    assert module.main(['--snapshot-url', origin+'/good']) == 1
    assert len(seen) == 1
    assert before == {p.name:p.read_bytes() for p in module.DATASET_ROOT.iterdir()}


@pytest.mark.parametrize('route,message', [('/wrong','pinned digest'),('/redirect','redirects'),
    ('/error','HTTP 403'),('/huge','byte limit'),('/interrupt','interrupted'),('/badlength','invalid Content-Length')])
def test_failed_fetch_never_publishes(acquisition, capsys, route, message):
    module, origin, seen = acquisition
    assert module.main(['--snapshot-url', origin+route+'?secret=private-token']) == 1
    captured = capsys.readouterr()
    assert message in captured.err
    assert 'private-token' not in captured.err and 'secret-length-token' not in captured.err
    assert not module.DATASET_ROOT.exists()
    assert len(seen) == 1


def test_fetch_uses_existing_atomic_no_replace_import(acquisition, monkeypatch):
    module, origin, _ = acquisition
    publish = module._publish_snapshot
    def race(stage, root):
        root.mkdir()
        publish(stage, root)
    monkeypatch.setattr(module, '_publish_snapshot', race)
    with pytest.raises(ValueError, match='already exists'):
        module.fetch_snapshot(origin+'/good')
    assert list(module.DATASET_ROOT.iterdir()) == []


def test_import_rechecks_downloaded_bytes(acquisition, monkeypatch):
    module, origin, _ = acquisition
    original = module.import_pool
    def replace(source, **kwargs):
        source.write_bytes(b'replaced downloaded bytes')
        return original(source, **kwargs)
    monkeypatch.setattr(module, 'import_pool', replace)
    with pytest.raises(ValueError, match='pinned digest'):
        module.fetch_snapshot(origin+'/good')
    assert not module.DATASET_ROOT.exists()


@pytest.mark.parametrize('url', ['http://user:private-token@localhost/a', 'file:///tmp/a',
    'https://host/a#private-token', 'https://host:bad/a'])
def test_invalid_urls_do_not_expose_credentials(acquisition, url, capsys):
    module, _, seen = acquisition
    assert module.main(['--snapshot-url', url]) == 1
    assert 'private-token' not in capsys.readouterr().err
    assert not seen and not module.DATASET_ROOT.exists()


def test_stream_without_length_is_still_size_bounded(acquisition, monkeypatch):
    module, origin, _ = acquisition
    monkeypatch.setattr(module, 'SNAPSHOT_MAX_BYTES', len(BODY)-1)
    with pytest.raises(ValueError, match='byte limit'):
        module.fetch_snapshot(origin+'/good')
    assert not module.DATASET_ROOT.exists()


def test_total_time_limit_fails_without_publication(acquisition, monkeypatch):
    module, origin, _ = acquisition
    monkeypatch.setattr(module, 'SNAPSHOT_TOTAL_SECONDS', 0.000001)
    with pytest.raises(ValueError, match='total time limit'):
        module.fetch_snapshot(origin+'/good')
    assert not module.DATASET_ROOT.exists()


def test_hard_deadline_includes_waiting_for_headers(acquisition, monkeypatch):
    module, origin, seen = acquisition
    monkeypatch.setattr(module, 'SNAPSHOT_TOTAL_SECONDS', 1)
    started = time.monotonic()
    with pytest.raises(ValueError, match='total time limit'):
        module.fetch_snapshot(origin+'/slow-headers?secret=private-token')
    assert time.monotonic()-started < 1.8
    assert seen == ['/slow-headers?secret=private-token']
    assert not module.DATASET_ROOT.exists()


@pytest.mark.parametrize('route,success', [('/good', True), ('/error', False)])
def test_explicit_proxy_and_ca_configuration_only(acquisition, monkeypatch, capsys, route, success):
    module, proxy_origin, seen = acquisition
    proxy = proxy_origin.replace('http://', 'http://proxy-user:proxy-secret@')
    monkeypatch.setenv('HTTP_PROXY', proxy)
    monkeypatch.setenv('http_proxy', proxy)
    monkeypatch.setenv('HTTPS_PROXY', proxy)
    monkeypatch.setenv('https_proxy', proxy)
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    # These explicit CA paths are forwarded without disabling TLS validation.
    monkeypatch.setenv('SSL_CERT_FILE', '/operator/ca-bundle.pem')
    monkeypatch.setenv('SSL_CERT_DIR', '/operator/ca-directory')
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        monkeypatch.setenv(key, 'private-model-key')
    original = module.subprocess.run
    calls = []
    def inspect(*args, **kwargs):
        env = kwargs['env']
        assert env['HTTP_PROXY'] == proxy and env['http_proxy'] == proxy
        assert env['HTTPS_PROXY'] == proxy and env['https_proxy'] == proxy
        assert env['NO_PROXY'] == env['no_proxy'] == ''
        assert env['SSL_CERT_FILE'] == '/operator/ca-bundle.pem'
        assert env['SSL_CERT_DIR'] == '/operator/ca-directory'
        assert 'private-model-key' not in json.dumps(env)
        assert not any(key in env for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','HF_TOKEN','HUGGING_FACE_HUB_TOKEN'))
        calls.append(env)
        return original(*args, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', inspect)
    # The local proxy answers this absolute URI itself; no external DNS or
    # remote endpoint is contacted, including on HTTP errors.
    url = 'http://operator-snapshot.invalid'+route+'?secret=query-token'
    assert module.main(['--snapshot-url', url]) == (0 if success else 1)
    assert len(calls) == 1 and seen == [url]
    captured = capsys.readouterr()
    public = captured.out+captured.err
    if success:
        public += (module.DATASET_ROOT/'pool.snapshot.json').read_text()
        assert json.loads((module.DATASET_ROOT/'pool.snapshot.json').read_text())['source_origin'] == 'http://operator-snapshot.invalid'
    else:
        assert not module.DATASET_ROOT.exists()
        assert 'HTTP 403' in public
    assert not any(secret in public for secret in ('proxy-secret','proxy-user','query-token','private-model-key', '/good', '/error'))
