"""Provider adapters and recording contract for the host web gateway."""
from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import threading
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import tools.web_gateway as gateway_mod  # noqa: E402
from tools.web_gateway import ProviderError, WebGateway, parse_fetched, parse_searxng, parse_serper  # noqa: E402
import eval.containers.container as container  # noqa: E402
import eval.service.services as services  # noqa: E402
from eval.run.runner import run_one  # noqa: E402
from data.scaffolds import registry  # noqa: E402
from policy_assertions import assert_policy_refusal

DEMO_IMAGE = os.environ.get("HG_EXEC_DEMO_IMAGE", "docker.m.daocloud.io/library/python:3.11-slim")


def test_recorded_provider_fixtures_have_common_shape():
    searx = {"results": [{"title": "A", "url": "https://a", "content": "alpha"}]}
    serper = {"organic": [{"title": "B", "link": "https://b", "snippet": "beta"}]}
    assert parse_searxng(searx) == [{"title": "A", "url": "https://a", "snippet": "alpha"}]
    assert parse_serper(serper) == [{"title": "B", "url": "https://b", "snippet": "beta"}]
    assert parse_fetched(200, "https://a", b"<title>A</title>") == {
        "url": "https://a", "status": 200, "text": "<title>A</title>", "bytes": 16}


def test_web_declaration_survives_environment_normalization():
    spec = {"kind": "exec", "image": "x", "network": "services",
            "services": [], "web": {"search": True, "fetch": False}}
    assert registry._normalize_env(spec)["web"] == {"search": True, "fetch": False}


def test_gateway_records_search_and_fetch_but_not_health(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_WEB_SEARCH", "none")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    record = tmp_path / "requests.jsonl"
    monkeypatch.setattr(gateway_mod, "_fetch_url", lambda url: (200, b"ok"))
    gateway = WebGateway(record_path=record).start()
    try:
        import urllib.request
        req = urllib.request.Request(gateway.url + "/health")
        urllib.request.urlopen(req).read()
        req = urllib.request.Request(gateway.url + "/search", data=b'{"query":"x","num":1}', method="POST")
        urllib.request.urlopen(req).read()
        req = urllib.request.Request(gateway.url + "/fetch", data=b'{"url":"https://example.test/"}', method="POST")
        urllib.request.urlopen(req).read()
    finally:
        gateway.stop()
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    assert [(r["kind"], r["provider"]) for r in rows] == [("search", "none"), ("fetch", "http")]
    assert rows[0]["status"] == 200 and rows[0]["bytes"] == 0
    assert rows[1]["bytes"] == 2


def test_fetch_rejects_non_http_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_WEB_SEARCH", "none")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    gateway = WebGateway(record_path=tmp_path / "requests.jsonl")
    with pytest.raises(Exception, match="http or https"):
        gateway.fetch_request("file:///etc/passwd")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://2130706433/", "http://0x7f000001/",
    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/",
])
def test_fetch_rejects_private_and_alternative_ip_destinations(url, monkeypatch):
    monkeypatch.setenv("HG_WEB_SEARCH", "none")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    gateway = WebGateway()
    with pytest.raises(ProviderError, match="public"):
        gateway.fetch_request(url)


class _FakeResponse:
    def __init__(self, status=200, body=b"ok", headers=None):
        self.status = self.code = status
        self.msg = "OK"
        self.headers = headers or {}
        self._body = body

    def read(self, _limit=-1):
        return self._body

    def info(self):
        return self.headers

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_fetch_allows_checked_external_url_with_mocked_opener(monkeypatch):
    monkeypatch.setenv("HG_WEB_SEARCH", "none")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    gateway = WebGateway()
    mock_dns(monkeypatch)
    monkeypatch.setattr(gateway_mod, "_fetch_opener",
                        lambda *_args: type("O", (), {"open": lambda _self, _req, timeout: _FakeResponse()})())
    assert gateway.fetch_request("https://example.test/page")["status"] == 200


def test_fetch_rejects_redirect_to_private(monkeypatch):
    monkeypatch.setenv("HG_WEB_SEARCH", "none")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    gateway = WebGateway()
    mock_dns(monkeypatch)
    monkeypatch.setattr(gateway_mod, "_fetch_opener", lambda *_args: type(
        "O", (), {"open": lambda _self, _req, timeout: _FakeResponse(
            302, b"redirect", {"Location": "http://127.0.0.1/private"})})())
    with pytest.raises(ProviderError, match="redirect rejected"):
        gateway.fetch_request("http://example.test/start")


def test_searxng_failed_engine_is_not_an_empty_measured_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_WEB_SEARCH", "searxng")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    body = json.dumps({"results": [], "unresponsive_engines": [["wikidata", "timeout"]]}).encode()
    monkeypatch.setattr(gateway_mod, "_json_request", lambda *args, **kwargs: (200, body))
    record = tmp_path / "requests.jsonl"
    gateway = WebGateway(record_path=record)
    with pytest.raises(ProviderError, match="failed engines"):
        gateway.search_request("broken")
    row = json.loads(record.read_text())
    assert row["status"] == 200 and row["bytes"] == len(body)


def test_healthy_searxng_zero_results_is_allowed(monkeypatch):
    monkeypatch.setenv("HG_WEB_SEARCH", "searxng")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    monkeypatch.setattr(gateway_mod, "_json_request",
                        lambda *args, **kwargs: (200, b'{"results": []}'))
    assert WebGateway().search_request("no hits") == {"results": []}


def test_paid_provider_without_key_refuses_to_start():
    env = os.environ.copy()
    env["HG_WEB_SEARCH"] = "serper"
    env.pop("SERPER_API_KEY", None)
    proc = subprocess.run([sys.executable, "tools/web_gateway.py", "--port", "0"], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode != 0
    assert "serper" in proc.stderr.lower()


def test_unobserved_admission_and_synthetic_web_isolation_backend(tmp_path, monkeypatch):
    """A negative connection probe against a confirmed live, named destination."""
    if container.available() is None:
        pytest.skip("no docker daemon")
    try:
        image = container.resolve_digest(DEMO_IMAGE)
    except container.ContainerUnavailable:
        pytest.skip("demo exec image is not available")
    monkeypatch.setenv("HG_WEB_SEARCH", "none")
    monkeypatch.setenv("HG_WEB_FETCH", "http")
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({
        "name": "web-probe", "version": "1", "entrypoint": "agent.py",
        "env_kinds": ["exec"]}))
    (repo / "agent.py").write_text("""
import argparse, json, os, pathlib, urllib.request, urllib.error
p=argparse.ArgumentParser(); p.add_argument('--task'); p.add_argument('--workdir'); a=p.parse_args()
w=pathlib.Path(a.workdir); base=os.environ.get('HG_SVC_WEB_URL')
probe=json.loads((w/'task.json').read_text())['probe_url']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
if base:
    req=urllib.request.Request(base+'/search', data=b'{"query":"x","num":1}', method='POST')
    result=json.loads(opener.open(req, timeout=5).read())
    answer='searchable' if result == {'results': []} else 'bad'
else:
    req=urllib.request.Request(probe+'/search', data=b'{"query":"x","num":1}', method='POST')
    try:
        opener.open(req, timeout=2).read()
        answer='reachable'
    except urllib.error.HTTPError:
        answer='reachable-http-error'
    except urllib.error.URLError:
        answer='absent-and-blocked'
(w/'answer.txt').write_text(answer); (w/'trace.jsonl').write_text('{}\\n')
""")
    common = {"kind": "exec", "image": image, "workdir": "/app",
              "placement": "inside", "services": []}
    # Pin current production refusal before exercising the synthetic backend.
    for policy in ({"network": "none"},
                   {"network": "services", "web": {"search": True, "fetch": True}}):
        refused = run_one(repo, {"task_id": "refused", "goal": "synthetic"},
                          env={**common, **policy}, recordings_root=tmp_path / "recordings")
        assert_policy_refusal(refused, ("env.exec_defaults", "env.network"))
        assert "web_gateway" not in refused
    assert not (tmp_path / "recordings").exists()
    # Only this authored HTTP fixture reaches the dormant backend. No production
    # observer, pilot, dataset score or admission override is exposed by the platform.
    monkeypatch.setattr("eval.run.runner._refuse_unobserved_policies", lambda *a, **k: None)
    import uuid
    network = services.create_network("web-isolation-" + uuid.uuid4().hex, "live-probe")
    try:
        probe = WebGateway(bind_host=services.gateway_ip(network)).start()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            # The host confirms liveness before and after the isolated container probe.
            assert json.loads(opener.open(probe.url + '/health').read())["ok"]
            with_web = run_one(repo, {"task_id": "with", "goal": "web", "probe_url": probe.url},
                               sandbox=True,
                               env={**common, "network": "services", "web": {"search": True, "fetch": True}},
                               run_id="web-cap", run_seed="s", recordings_root=tmp_path / "recordings")
            without_web = run_one(repo, {"task_id": "without", "goal": "offline", "probe_url": probe.url},
                                  sandbox=True, env={**common, "network": "none"}, run_id="web-cap",
                                  run_seed="s", recordings_root=tmp_path / "recordings")
            assert json.loads(opener.open(probe.url + '/health').read())["ok"]
        finally:
            probe.stop()
    finally:
        assert services.remove_network(network) in (None, [])
    assert with_web["exit_code"] == without_web["exit_code"] == 0, (with_web, without_web)
    assert with_web["answer"] == "searchable"
    assert with_web["web_gateway"]["published"] is True
    assert with_web["web_gateway"]["search"] == "none"
    assert without_web["answer"] == "absent-and-blocked"
    assert without_web["web_gateway"] is None
    rows = [json.loads(line) for line in (tmp_path / "recordings/with/web/requests.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["provider"] == "none"



def mock_dns(monkeypatch, addresses=None):
    addresses = addresses or ['93.184.216.34']
    def resolve(host, port, **kwargs):
        import ipaddress
        try:
            ips = [str(ipaddress.ip_address(host))]
        except ValueError:
            ips = addresses
        return [(socket.AF_INET6 if ':' in ip else socket.AF_INET,
                 socket.SOCK_STREAM, 6, '', (ip, port)) for ip in ips]
    monkeypatch.setattr(gateway_mod.socket, 'getaddrinfo', resolve)


@pytest.mark.parametrize('addresses', [
    ['127.0.0.1'], ['10.1.2.3'], ['172.16.0.1'], ['192.168.1.1'],
    ['169.254.169.254'], ['100.100.100.200'], ['::1'], ['fe80::1'], ['fc00::1'],
    ['93.184.216.34', '10.0.0.1'], ['2002:7f00:1::'], ['64:ff9b::7f00:1'],
])
def test_dns_rejects_any_private_answer(monkeypatch, addresses):
    mock_dns(monkeypatch, addresses)
    with pytest.raises(ProviderError, match='not public'):
        gateway_mod._public_target('https://example.test/')


@contextmanager
def fixture_server(status=200, body=b'ok', headers=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_real_transport_pins_ip_and_does_not_resolve_hostname_twice(monkeypatch):
    monkeypatch.setenv('HG_WEB_PROXY', '')
    resolutions, connections = [], []
    real_connect = socket.create_connection
    with fixture_server() as port:
        def resolve(host, target_port, **kwargs):
            resolutions.append(host)
            ip = '93.184.216.34' if len(resolutions) == 1 else '127.0.0.1'
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, target_port))]
        def connect(destination, timeout):
            connections.append(destination)
            # Map the pinned public destination to our local deterministic fixture only.
            with monkeypatch.context() as scoped:
                scoped.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))])
                return real_connect(('127.0.0.1', port), timeout)
        monkeypatch.setattr(socket, 'getaddrinfo', resolve)
        monkeypatch.setattr(socket, 'create_connection', connect)
        assert gateway_mod._fetch_url('http://rebind.test/') == (200, b'ok')
    assert resolutions == ['rebind.test']
    assert connections == [('93.184.216.34', 80)]


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_proxy_connect_uses_pinned_ip_and_https_keeps_original_tls_name(monkeypatch, scheme):
    monkeypatch.setenv('HG_WEB_PROXY', 'http://proxy.test:8080')
    destinations, sent, tls_names = [], [], []
    fake_socket = SimpleNamespace(
        sendall=lambda data: sent.append(data), close=lambda: None,
        makefile=lambda *args: io.BytesIO(b'HTTP/1.0 200 Connected\r\n\r\n'))
    monkeypatch.setattr(socket, 'create_connection',
                        lambda destination, timeout: destinations.append(destination) or fake_socket)
    monkeypatch.setattr(gateway_mod.ssl, 'create_default_context', lambda: SimpleNamespace(
        wrap_socket=lambda sock, server_hostname: tls_names.append(server_hostname) or sock))
    def do_open(handler, connection_class, request):
        connection = connection_class(request.host, timeout=1)
        connection.connect()
        return _FakeResponse()
    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, 'do_open', do_open)
    gateway_mod._fetch_opener('example.test', 443 if scheme == 'https' else 80,
                              '93.184.216.34').open(scheme + '://example.test/')
    assert destinations == [('proxy.test', 8080)]
    assert b'CONNECT 93.184.216.34:' in b''.join(sent)
    assert b'example.test' not in b''.join(sent)
    assert tls_names == (['example.test'] if scheme == 'https' else [])


def test_redirect_handler_cannot_bypass_private_policy_and_preserves_failed_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_WEB_PROXY', '')
    monkeypatch.setenv('HG_WEB_SEARCH', 'none')
    monkeypatch.setenv('HG_WEB_FETCH', 'http')
    mock_dns(monkeypatch)
    real_connect = socket.create_connection
    connections = []
    with fixture_server(302, b'redirect body', {'Location': 'http://127.0.0.1/private'}) as port:
        def connect(destination, timeout):
            connections.append(destination)
            with monkeypatch.context() as scoped:
                scoped.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))])
                return real_connect(('127.0.0.1', port), timeout)
        monkeypatch.setattr(socket, 'create_connection', connect)
        gateway = WebGateway(record_path=tmp_path / 'requests.jsonl')
        try:
            with pytest.raises(ProviderError, match='redirect rejected'):
                gateway.fetch_request('http://example.test/')
        finally:
            gateway.server.server_close()
    assert connections == [('93.184.216.34', 80)]
    row = json.loads((tmp_path / 'requests.jsonl').read_text())
    assert row['status'] == 302 and row['bytes'] == len(b'redirect body') and row['error']


@pytest.mark.parametrize('payload', [b'<html>error</html>', b'{}', b'[]',
    b'{"results": null}', b'{"results": [null]}',
    b'{"results": [], "unresponsive_engines": [["google", "ConnectError"]]}'])
def test_searxng_faults_return_structured_http_error_and_record_upstream(tmp_path, monkeypatch, payload):
    monkeypatch.setenv('HG_WEB_SEARCH', 'searxng')
    monkeypatch.setenv('HG_WEB_FETCH', 'http')
    monkeypatch.setattr(gateway_mod, '_json_request', lambda *args, **kwargs: (200, payload))
    record = tmp_path / 'requests.jsonl'
    gateway = WebGateway(record_path=record).start()
    try:
        request = urllib.request.Request(gateway.url + '/search', data=b'{"query":"fault"}')
        with pytest.raises(urllib.error.HTTPError) as failed:
            urllib.request.urlopen(request)
        assert failed.value.code == 502
        result = json.loads(failed.value.read())
        assert result['status'] == 'error' and result['upstream_status'] == 200
    finally:
        gateway.stop()
    row = json.loads(record.read_text())
    assert row['status'] == 200 and row['bytes'] == len(payload) and row['error']


def test_searxng_local_endpoint_survives_and_healthy_zero_is_http_200(monkeypatch):
    monkeypatch.setenv('HG_WEB_SEARCH', 'searxng')
    monkeypatch.setenv('HG_WEB_FETCH', 'http')
    with fixture_server(body=b'{"results": [], "unresponsive_engines": []}') as port:
        monkeypatch.setenv('HG_WEB_SEARXNG_URL', f'http://127.0.0.1:{port}')
        gateway = WebGateway().start()
        try:
            req = urllib.request.Request(gateway.url + '/search', data=b'{"query":"zero"}')
            with urllib.request.urlopen(req) as response:
                assert response.status == 200 and json.loads(response.read()) == {'results': []}
        finally:
            gateway.stop()


def test_searxng_connection_failure_has_status_zero_and_error_record(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_WEB_SEARCH', 'searxng')
    monkeypatch.setenv('HG_WEB_FETCH', 'http')
    def unavailable(*args, **kwargs):
        raise urllib.error.URLError('ConnectError fixture')
    monkeypatch.setattr(gateway_mod, '_json_request', unavailable)
    record = tmp_path / 'requests.jsonl'
    gateway = WebGateway(record_path=record)
    try:
        with pytest.raises(ProviderError, match='ConnectError'):
            gateway.search_request('fault')
    finally:
        gateway.server.server_close()
    row = json.loads(record.read_text())
    assert row['status'] == row['bytes'] == 0 and row['error']


@pytest.mark.parametrize('provider,endpoint', [('searxng', 'http://127.0.0.1:19999'), ('none', None)])
def test_driver_writes_real_run_meta_before_task_launch(tmp_path, monkeypatch, provider, endpoint):
    import driver
    from harnessgrad import config
    config.reset()
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_AGENT_MODEL', 'dummy')
    monkeypatch.setenv('HG_WEB_SEARCH', provider)
    monkeypatch.setenv('HG_WEB_FETCH', 'http')
    monkeypatch.setenv('HG_WEB_SEARXNG_URL', endpoint or 'http://unused.test')
    monkeypatch.setattr(driver, 'load_env', lambda *_args: {})
    task = {'task_id': 'fixture', 'goal': 'probe'}
    env = {'fixture': {'kind': 'exec', 'image': 'fixture', 'network': 'services',
                       'web': {'search': True, 'fetch': True}}}
    monkeypatch.setattr(driver.datasets, 'load_split', lambda *_args: ([task], {}, None))
    monkeypatch.setattr(driver.datasets, 'load_tasks', lambda *_args, **kwargs: ([task], {}, {}))
    monkeypatch.setattr(driver.datasets, 'load_envs', lambda *_args, **kwargs: env)
    monkeypatch.setattr(driver.datasets, 'oracle_source', lambda *_args: ('released', 'fixture'))
    monkeypatch.setattr(driver, '_resolve_envs', lambda specs: (specs, None))
    monkeypatch.setattr(driver.container, 'available', lambda: 'fixture')
    monkeypatch.setattr(driver.container, 'self_check', lambda *_args: {'ok': True, 'detail': 'fixture'})
    monkeypatch.setattr(driver, '_warn_missing_overlay', lambda *_args: None)
    monkeypatch.setattr(driver.services_mod, 'reap', lambda *_args: [])
    class BeforeTaskLaunch(Exception):
        pass
    def inspect_meta(*args):
        meta = json.loads((tmp_path / 'runs/meta/run_meta.json').read_text())
        assert meta['web_search_provider'] == provider
        assert meta['web_fetch_provider'] == 'http'
        assert meta.get('web_searxng_url') == endpoint
        assert meta['env']['web']['search_provider'] == provider
        raise BeforeTaskLaunch()
    monkeypatch.setattr(driver, 'stage_workspace', inspect_meta)
    monkeypatch.setattr(sys, 'argv', ['driver.py', '--harness', 'oracle', '--dataset', 'demo',
        '--rounds', '0', '--sampling', 'all', '--improver', 'none', '--no-sandbox', '--run-id', 'meta',
        '--runs-root', str(tmp_path / 'runs'), '--work-root', str(tmp_path / 'work')])
    try:
        with pytest.raises(BeforeTaskLaunch):
            driver.main()
    finally:
        config.reset()
