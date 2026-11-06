"""Retired legacy HTTP entry points must never reach side-effecting helpers."""
import json
from unittest.mock import Mock

import pytest

from tools import serve_ui


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("path", [
    "/api/settings/save", "/api/prepare/run", "/api/promote",
    "/api/test-connection", "/api/explain", "/api/options",
    "/api/run", "/api/stop/existing",
])
@pytest.mark.parametrize("query", ["", "?cfg=%7Bbad&round=not-an-int&as=..&token=synthetic-secret"])
def test_retired_routes_are_inert(tmp_path, monkeypatch, method, path, query):
    evidence = tmp_path / "evidence"
    evidence.write_text("keep")
    def forbidden(*args, **kwargs):
        raise AssertionError("retired endpoint invoked side effect")
    for name in ("write_settings", "start_prepare", "promote", "test_connection",
                 "explain_diff", "scan_options", "start_job", "stop_job"):
        monkeypatch.setattr(serve_ui, name, forbidden)
    monkeypatch.setattr(serve_ui.subprocess, "Popen", forbidden)
    monkeypatch.setattr(serve_ui.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(serve_ui.os, "kill", forbidden)
    monkeypatch.setattr(serve_ui.os, "killpg", forbidden)
    monkeypatch.setattr(serve_ui.Path, "write_text", forbidden)
    handler = object.__new__(serve_ui.Handler)
    handler.path = path + query
    handler._json = Mock()
    getattr(handler, "do_" + method)()
    body, status = handler._json.call_args.args
    assert status == 410
    assert "Console" in body["error"]
    assert "synthetic-secret" not in json.dumps(body)
    assert evidence.read_text() == "keep"


def test_options_never_resolves_secret_improver(monkeypatch):
    def forbidden():
        raise AssertionError("improver resolution must not execute")
    monkeypatch.setattr(serve_ui, "improver_options", forbidden)
    handler = object.__new__(serve_ui.Handler)
    handler.path = "/api/options?url=https://user:synthetic-secret@example.invalid"
    handler._json = Mock()
    handler.do_GET()
    body, status = handler._json.call_args.args
    assert status == 410
    assert "synthetic-secret" not in json.dumps(body)


def test_post_never_delegates_to_read_helpers(monkeypatch):
    monkeypatch.setattr(serve_ui, "prepare_report", lambda *args: pytest.fail("read helper called"))
    handler = object.__new__(serve_ui.Handler)
    handler.path = "/api/prepare?dataset=invalid"
    handler._json = Mock()
    handler.do_POST()
    assert handler._json.call_args.args[1] == 405
