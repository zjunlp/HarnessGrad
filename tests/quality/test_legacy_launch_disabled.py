"""Offline regression tests for the retired legacy launcher."""
import json
from unittest.mock import Mock

import pytest

from tools import serve_ui


@pytest.mark.parametrize("capacity", ["1", "2", "", "invalid"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_legacy_routes_never_launch_or_signal(tmp_path, monkeypatch, capacity, method):
    budget = tmp_path / "budget"
    budget.mkdir()
    (budget / "capacity").write_text("1\n")
    monkeypatch.setenv("HG_CONSOLE_BUDGET_DIR", str(budget))
    monkeypatch.setenv("HG_CONSOLE_BUDGET_CAPACITY", capacity)
    monkeypatch.setattr(serve_ui, "RUNS", tmp_path)
    keep = tmp_path / "existing"
    keep.mkdir()
    (keep / "evidence").write_text("preserve")
    monkeypatch.setattr(serve_ui.subprocess, "Popen", Mock(side_effect=AssertionError("spawn")))
    monkeypatch.setattr(serve_ui.os, "killpg", Mock(side_effect=AssertionError("signal")))
    cfg = {"run_id": "existing", "overwrite": True}
    assert "disabled" in serve_ui.start_job(cfg)["error"]
    assert "disabled" in serve_ui.stop_job("existing")["error"]
    for path in ("/api/run?cfg=invalid", "/api/stop/existing"):
        handler = object.__new__(serve_ui.Handler)
        handler.path = path
        handler._json = Mock()
        getattr(handler, "do_" + method)()
        response, status = handler._json.call_args.args
        assert status == 410
        assert "Console" in response["error"]
    assert (keep / "evidence").read_text() == "preserve"
    serve_ui.subprocess.Popen.assert_not_called()
    serve_ui.os.killpg.assert_not_called()


def test_options_never_exposes_model_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("HG_AGENT_MODEL=test\nHG_AGENT_BASE_URL=https://example.invalid\nHG_AGENT_API_KEY=synthetic-secret\n")
    for name in ("methods", "base_harness", "data/datasets"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(serve_ui, "ROOT", tmp_path)
    monkeypatch.setattr(serve_ui.registry, "published", lambda: [])
    monkeypatch.setattr(serve_ui, "improver_options", lambda: {})
    assert "synthetic-secret" not in json.dumps(serve_ui.model_presets())
    handler = object.__new__(serve_ui.Handler)
    handler.path = "/api/options"
    handler._json = Mock()
    handler.do_GET()
    assert "synthetic-secret" not in json.dumps(handler._json.call_args.args[0])
