"""Synthetic HTTP controls; no model, dataset, scorer or inference is invoked."""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.containers import model_route, sandbox
from eval.policy.runtime_policy import admit_native_network
from eval.service.modelgate import GatewayUnavailable
from eval.service.native_model_route import NativeModelRoute
from eval.run.runner import run_one
from eval.policy.unobserved_diagnostic import Authorization


@pytest.fixture
def upstream():
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, self.headers["Host"], body))
            self.send_response(201)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(params=[False, True], ids=['legacy', 'observed'])
def route(upstream, request):
    route = NativeModelRoute(upstream[0], observe_http=request.param).start()
    try:
        yield route
    finally:
        route.stop()


def layout(tmp_path):
    work = tmp_path / "work"
    task = tmp_path / "task"
    work.mkdir()
    task.mkdir()
    return dict(platform=ROOT, work_root=work, repo=work, task_dir=task)


def test_native_policy_matrix():
    for network in (None, "none"):
        for backend in ("mock", "openai"):
            for manifest in ({}, {"role": "control"}):
                admit_native_network({"network": network}, manifest, backend)
    for network in ("bridge", "services", "host", "bogus"):
        with pytest.raises(ValueError, match="unsupported native sandbox network policy"):
            admit_native_network({"network": network}, {}, "openai")


@pytest.mark.parametrize("url", ["https://example.com/v1", "unix:///model.sock",
                                  "http://user:pass@localhost/v1", "http://localhost/v1?q=1",
                                  "http://localhost/v1#fragment", ""])
def test_unrepresentable_urls_fail_closed(url):
    with pytest.raises(GatewayUnavailable):
        NativeModelRoute(url)


def test_private_socket_and_cleanup(route):
    directory, path = route.directory, route.socket_path
    assert directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(path))
    client.settimeout(3)
    client.sendall(b"POST /v1/chat/completions HTTP/1.1\r\n")  # open incomplete request
    route.stop()
    try:
        assert client.recv(1) == b""
    except ConnectionResetError:
        pass  # Closing a socket with an unread request may report reset, not EOF.
    client.close()
    assert not directory.exists()
    assert route.thread is None and route.server is None
    route.stop()  # finally/timeout cleanup is idempotent


def test_validated_mount_rejects_aliases_wrong_kind_and_unisolated_route(tmp_path, route):
    ordinary = tmp_path / "ordinary"
    ordinary.write_text("not a socket")
    alias = tmp_path / "alias"
    alias.symlink_to(route.socket_path)
    for source in (ordinary, alias, Path("relative.sock")):
        with pytest.raises(GatewayUnavailable):
            model_route.mounts(source, network="none", exposed_paths=(), writable_paths=())
    for network in (None, "bridge", "services"):
        with pytest.raises(GatewayUnavailable):
            model_route.mounts(route.socket_path, network=network, exposed_paths=(), writable_paths=())
    for path in (Path("/run"), model_route.ROOT, model_route.ROOT / "child"):
        with pytest.raises(GatewayUnavailable, match="overlaps"):
            model_route.mounts(route.socket_path, network="none", exposed_paths=(path,), writable_paths=())
    with pytest.raises(GatewayUnavailable, match="writable"):
        model_route.mounts(route.socket_path, network="none", exposed_paths=(),
                           writable_paths=(route.directory,))


def test_launch_revalidates_replaced_socket(tmp_path, route):
    paths = layout(tmp_path)
    route.socket_path.unlink()
    route.socket_path.write_text("replaced")
    with pytest.raises(GatewayUnavailable, match="UNIX socket"):
        sandbox.command(["true"], **paths, env={}, network="none", model_socket=route.socket_path)


def test_live_namespace_http_preserves_payload_and_fixed_upstream(tmp_path, route, upstream):
    paths = layout(tmp_path)
    code = '''import http.client, json, os, pathlib, urllib.parse
base = urllib.parse.urlsplit(os.environ['HG_AGENT_BASE_URL'])
c = http.client.HTTPConnection(base.hostname, base.port, timeout=5)
body = b'x' * 131073
for target in (base.path + '/chat/completions', 'http://203.0.113.1/escape'):
    c.request('POST', target, body=body, headers={'Host':'203.0.113.1'})
    r = c.getresponse()
    assert r.status == 201 and r.read() == body
c.close()
try:
    pathlib.Path('/run/harnessgrad-model/forwarder.py').write_text('tamper')
except OSError:
    pass
else:
    raise AssertionError('trusted forwarder is writable')
print(json.dumps({'url':os.environ['HG_AGENT_BASE_URL'], 'net':os.readlink('/proc/self/ns/net')}))
'''
    plan = sandbox.command(["/usr/bin/python3", "-c", code], **paths, env={},
                           network="none", model_socket=route.socket_path, model_path=route.path)
    assert "--unshare-net" in plan
    assert plan.index(str(model_route.ROOT / "model.sock")) > plan.index("--bind")
    proc = subprocess.run(plan, capture_output=True, text=True, timeout=15, env={})
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["url"].startswith("http://127.0.0.1:") and result["url"].endswith("/v1")
    assert result["net"] != os.readlink("/proc/self/ns/net")
    assert [(p, h) for p, h, _ in upstream[1]] == [
        ("/v1/chat/completions", "203.0.113.1"), ("http://203.0.113.1/escape", "203.0.113.1")]
    assert route.account()["connections"] == 1
    if route.http is not None:
        report = route.http.report()
        assert report['complete'], report
        assert [c['status'] for c in report['calls']] == [201, 201]


@pytest.mark.parametrize("authorized", [False, True])
def test_runner_live_backend_wiring_and_policy_gate(tmp_path, upstream, monkeypatch, authorized):
    repo = tmp_path / "work/workspace"
    repo.mkdir(parents=True)
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "agent.py"}))
    (repo / "agent.py").write_text('''import argparse, os, pathlib, urllib.request
p=argparse.ArgumentParser(); p.add_argument('--task'); p.add_argument('--workdir'); a=p.parse_args()
request=urllib.request.Request(os.environ['HG_AGENT_BASE_URL']+'/chat/completions', data=b'unchanged')
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request) as r:
    assert r.status == 201 and r.read() == b'unchanged'
pathlib.Path(a.workdir, 'answer.txt').write_text('route reached')
''')
    monkeypatch.setenv("HG_AGENT_BACKEND", "openai")
    monkeypatch.setenv("HG_AGENT_BASE_URL", upstream[0])
    task = {"task_id": "netroute-synthetic", "goal": "transport control"}
    env = {"kind": "files", "network": "none"}
    auth = Authorization("netroute-synthetic", task["task_id"], "synthetic local transport test")
    # Native offline isolation is applied by this process, so both paths must
    # actually reach the synthetic upstream without weakening the transport checks.
    result = run_one(repo, task, env=env, timeout_s=15, run_id=auth.run_id,
                     diagnostic_authorization=auth if authorized else None)
    assert "invalid" not in result
    assert result["exit_code"] == 0, result
    assert result["answer"] == "route reached"
    assert result["model_gateway"]["transport"] == "unix+isolated-loopback"
    assert result["model_gateway"]["connections"] == 1
    if authorized:
        assert result["enforcement"]["production_authorized"] is False
    else:
        assert "enforcement" not in result


def test_offline_without_sandbox_fails_before_execution(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "harness.json").write_text('{"entrypoint":"must-not-exist.py"}')
    monkeypatch.setenv("HG_AGENT_BACKEND", "openai")
    auth = Authorization("netroute-no-sandbox", "synthetic", "negative transport control")
    with pytest.raises(ValueError, match="isolation requires sandbox=True"):
        run_one(repo, {"task_id":"synthetic"}, env={"kind":"files", "network":"none"},
                sandbox=False, run_id=auth.run_id, diagnostic_authorization=auth)


def test_timeout_and_nonzero_child_cleanup(tmp_path, route):
    paths = layout(tmp_path)
    proc = sandbox.run(["/usr/bin/python3", "-c", "raise SystemExit(17)"], **paths, env={},
                       network="none", model_socket=route.socket_path, timeout_s=5)
    assert proc.returncode == 17
    with pytest.raises(subprocess.TimeoutExpired):
        sandbox.run(["/usr/bin/python3", "-c", "import time; time.sleep(30)"], **paths,
                    env={}, network="none", model_socket=route.socket_path, timeout_s=1)
