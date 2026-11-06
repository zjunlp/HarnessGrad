"""Synthetic loopback HTTP regressions for the console's browser security boundary.

No service or model is contacted: every sensitive handler is replaced by a recorder.
Requests use explicit headers so duplicates and malformed framing survive client encoding.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def console(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("console_http_security_server", ROOT / "console/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SETTINGS", tmp_path / "settings.json")
    for key in ("HG_CONSOLE_TASK_BASE_URL", "HG_CONSOLE_TASK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    original_probe = module.probe_endpoint
    monkeypatch.setattr(module, "probe_endpoint", lambda url, key: calls.append(("probe", url, key)) or {"ok": True})
    monkeypatch.setattr(module, "api_launch", lambda form: (calls.append(("launch", form)) or (200, {"ok": True})))
    monkeypatch.setattr(module, "api_explain", lambda *args, **kwargs: (calls.append(("explain", args)) or (200, {"ok": True})))
    monkeypatch.setattr(module, "launch_preview", lambda form: calls.append(("preview", form)) or {"ok": True})
    monkeypatch.setattr(module, "launch_form", lambda: {"defaults": {k: "fixture" for k in ("dataset", "harness", "rounds", "jobs", "trials", "sampling")}})
    httpd = module.ConsoleServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(module=module, httpd=httpd, calls=calls, original_probe=original_probe)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(console, method, path, headers=(), body=b"", host=True):
    port = console.httpd.server_port
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host:
            conn.putheader("Host", f"127.0.0.1:{port}")
        for name, value in headers:
            conn.putheader(name, value.format(port=port))
        conn.endheaders(body)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


JSON_HEADERS = [("Content-Type", "application/json"), ("Content-Length", "2")]


@pytest.mark.parametrize("method,path", [
    ("GET", "/"), ("GET", "/api/health"), ("GET", "/api/settings"),
    ("GET", "/api/launch/preview"), ("GET", "/api/explain?run=r&round=0"),
    ("POST", "/api/runs"), ("POST", "/api/settings/test"),
    ("DELETE", "/api/submissions/example"),
])
@pytest.mark.parametrize("host_headers", [
    [], [("Host", "127.0.0.1:1")],
    [("Host", "example.invalid:{port}")],
    [("Host", "127.0.0.1:{port}"), ("Host", "127.0.0.1:{port}")],
])
def test_all_routes_reject_missing_invalid_or_duplicate_host(console, method, path, host_headers):
    headers = host_headers + (JSON_HEADERS if method == "POST" else [])
    status, _ = request(console, method, path, headers, b"{}" if method == "POST" else b"", host=False)
    assert 400 <= status < 500
    assert not console.calls


@pytest.mark.parametrize("headers", [
    [("Origin", "http://evil.invalid")],
    [("Origin", "null")],
    [("Origin", "http://127.0.0.1:{port}"), ("Origin", "http://127.0.0.1:{port}")],
    [("Sec-Fetch-Site", "cross-site")],
    [("Origin", "http://127.0.0.1:{port}"), ("Sec-Fetch-Site", "cross-site")],
])
@pytest.mark.parametrize("method,path", [("POST", "/api/runs"), ("POST", "/api/settings/test"),
                                        ("GET", "/api/explain?run=r&round=0"),
                                        ("GET", "/api/launch/preview")])
def test_cross_site_or_ambiguous_origin_never_reaches_sensitive_handlers(console, method, path, headers):
    status, _ = request(console, method, path, headers + (JSON_HEADERS if method == "POST" else []),
                        b"{}" if method == "POST" else b"")
    assert 400 <= status < 500
    assert not console.calls


def test_localhost_host_is_accepted_for_ordinary_api(console):
    status, _ = request(console, "POST", "/api/launch/preview", [("Host", "localhost:{port}")] + JSON_HEADERS, b"{}", host=False)
    assert status == 200
    assert console.calls[0][0] == "preview"


def test_submission_upload_still_requires_numeric_host(console):
    status, _ = request(console, "POST", "/api/submissions", [("Host", "localhost:{port}")], host=False)
    assert 400 <= status < 500


def test_same_origin_json_launch_and_explain_reach_only_mocks(console):
    origin = [("Origin", "http://127.0.0.1:{port}"), ("Sec-Fetch-Site", "same-origin")]
    assert request(console, "POST", "/api/runs", origin + JSON_HEADERS, b"{}")[0] == 200
    assert request(console, "GET", "/api/explain?run=r&round=0", origin)[0] == 405
    body = b'{"run":"r","round":0}'
    assert request(console, "POST", "/api/explain", origin + [("Content-Type", "application/json"),
                   ("Content-Length", str(len(body)))], body)[0] == 200
    assert [call[0] for call in console.calls] == ["launch", "explain"]


@pytest.mark.parametrize("headers,body", [
    ([("Content-Length", "2")], b"{}"),
    ([("Content-Type", "text/plain"), ("Content-Length", "2")], b"{}"),
    ([("Content-Type", "application/x-www-form-urlencoded"), ("Content-Length", "2")], b"{}"),
    ([("Content-Type", "application/json"), ("Content-Type", "application/json"), ("Content-Length", "2")], b"{}"),
    ([("Content-Type", "application/json")], b""),
    ([("Content-Type", "application/json"), ("Content-Length", "2"), ("Content-Length", "2")], b"{}"),
    ([("Content-Type", "application/json"), ("Content-Length", "2"), ("Transfer-Encoding", "chunked")], b"{}"),
    ([("Content-Type", "application/json"), ("Content-Length", "-1")], b""),
    ([("Content-Type", "application/json"), ("Content-Length", "not-a-number")], b""),
])
@pytest.mark.parametrize("path", ["/api/runs", "/api/settings/test", "/api/explain"])
def test_json_endpoints_reject_ambiguous_type_or_framing(console, path, headers, body):
    status, _ = request(console, "POST", path, headers, body)
    assert 400 <= status < 500
    assert not console.calls


def post_json(console, path, value):
    body = json.dumps(value).encode()
    return request(console, "POST", path, [("Content-Type", "application/json; charset=utf-8"),
                                         ("Content-Length", str(len(body)))], body)


def test_stored_secret_is_bound_to_its_configured_endpoint(console):
    console.module.save_settings({"task": {"base_url": "https://stored.invalid/v1", "api_key": "synthetic-secret"}})
    status, _ = post_json(console, "/api/settings/test", {"side": "task"})
    assert status == 200
    assert console.calls == [("probe", "https://stored.invalid/v1", "synthetic-secret")]
    console.calls.clear()
    status, raw = post_json(console, "/api/settings/test", {"side": "task", "base_url": "https://other.invalid/v1"})
    assert not console.calls or console.calls == [("probe", "https://other.invalid/v1", "")]
    assert b"synthetic-secret" not in raw
    assert status in (200, 400, 403, 409, 422)


def test_explicit_key_for_different_endpoint_is_not_replaced(console):
    console.module.save_settings({"task": {"base_url": "https://stored.invalid/v1", "api_key": "synthetic-secret"}})
    status, raw = post_json(console, "/api/settings/test", {"side": "task", "base_url": "https://other.invalid/v1", "api_key": "explicit-key"})
    assert status == 200
    assert console.calls == [("probe", "https://other.invalid/v1", "explicit-key")]
    assert b"synthetic-secret" not in raw and b"explicit-key" not in raw


def test_probe_disables_redirects_and_sends_secret_only_to_configured_url(console, monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"data": [{"id": "synthetic-model"}]}'

    def build_opener(handler):
        assert handler.redirect_request(None, None, 302, "Found", {}, "https://other.invalid/") is None

        class Opener:
            def open(self, req, timeout):
                seen.append((req.full_url, req.get_header("Authorization"), timeout))
                return Response()
        return Opener()

    monkeypatch.setattr(console.module.urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(console.module, "probe_endpoint", console.original_probe)
    console.module.save_settings({"task": {"base_url": "https://stored.invalid/v1", "api_key": "synthetic-secret"}})
    status, raw = post_json(console, "/api/settings/test", {"side": "task"})
    assert status == 200 and json.loads(raw)["models"] == [] and json.loads(raw)["count"] == 1
    assert seen == [("https://stored.invalid/v1/models", "Bearer synthetic-secret", 12)]
    assert b"synthetic-secret" not in raw


def test_explain_range_post_preserves_arguments(console, monkeypatch):
    monkeypatch.setattr(console.module, 'api_explain_range',
                        lambda *args, **kwargs: (console.calls.append(('range', args, kwargs)) or (200, {'ok': True})))
    status, _ = post_json(console, '/api/explain', {'run': 'r', 'from': 0, 'to': 4, 'force': True})
    assert status == 200
    assert console.calls == [('range', ('r', 0, 4), {'force': True})]


def test_settings_probe_is_not_a_get_side_effect(console):
    status, _ = request(console, "GET", "/api/settings/test")
    assert 400 <= status < 500
    assert not console.calls
