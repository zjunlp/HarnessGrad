"""Legacy HTTP display must not disclose embedded endpoint credentials."""
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from tools import serve_ui


@pytest.mark.parametrize("url", [
    "https://user:synthetic-secret@example.invalid/v1",
    "https://example.invalid/v1?token=synthetic-secret",
    "https://example.invalid/v1#synthetic-secret",
    "https://[synthetic-secret",
    "https://example.invalid:bad/synthetic-secret",
    "https://proxy.invalid/synthetic-secret/v1",
])
def test_http_redacts_url_and_blank_roundtrip_preserves_it(tmp_path, monkeypatch, url):
    env = tmp_path / ".env"
    env.write_text(f"HG_AGENT_MODEL=test\nHG_AGENT_BASE_URL={url}\nHG_METHOD_BASE_URL={url}\nHG_AGENT_API_KEY=synthetic-key\nHG_METHOD_API_KEY=synthetic-key\n")
    for name in ("methods", "base_harness", "data/datasets"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(serve_ui, "ROOT", tmp_path)
    monkeypatch.setattr(serve_ui.registry, "published", lambda: [])
    monkeypatch.setattr(serve_ui, "improver_options", lambda: {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve_ui.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection(*server.server_address, timeout=5)
        for path in ("/api/options", "/api/settings"):
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode()
            assert resp.status == (410 if path == "/api/options" else 200)
            assert "synthetic-secret" not in body and "synthetic-key" not in body
            data = json.loads(body)
            if path == "/api/settings":
                for key in ("HG_AGENT_BASE_URL", "HG_METHOD_BASE_URL"):
                    assert data[key] == {"set": True, "value": "", "redacted": True}
            else:
                assert "Console" in data["error"]
                preset = serve_ui.model_presets()["配置的 OpenAI 兼容模型"]
                assert preset["HG_AGENT_BASE_URL"] == ""
                assert preset["HG_AGENT_BASE_URL_CONFIGURED"] is True
                assert preset["HG_AGENT_BASE_URL_REDACTED"] is True
        conn.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    before = env.read_text()
    serve_ui.write_settings({"HG_AGENT_BASE_URL": "", "HG_METHOD_BASE_URL": ""})
    assert env.read_text() == before
    serve_ui.write_settings({"HG_AGENT_BASE_URL": "https://replacement.invalid/v1"})
    settings = serve_ui.read_settings()
    assert settings["HG_AGENT_BASE_URL"] == {"set": True, "value": "https://replacement.invalid/v1", "redacted": False}
    assert settings["HG_METHOD_BASE_URL"]["redacted"] is True


def test_safe_url_unchanged_and_empty_not_redacted():
    assert serve_ui._display_url("http://example.invalid:123/v1") == ("http://example.invalid:123/v1", False)
    assert serve_ui._display_url("") == ("", False)
