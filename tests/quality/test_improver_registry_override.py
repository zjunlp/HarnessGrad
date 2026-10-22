"""Local registry selection must reach the driver and fail closed on bad paths."""
import json
from pathlib import Path

import pytest

from harnessgrad import config
from tools import improver


@pytest.fixture(autouse=True)
def clear_config():
    config.reset()
    yield
    config.reset()


def test_environment_selects_local_registry(monkeypatch, tmp_path):
    registry = tmp_path / "local.json"
    registry.write_text(json.dumps({"default": "local-dsh", "improvers": {}}))
    monkeypatch.setenv("HG_IMPROVER_CONFIG", str(registry))
    assert improver.load_config()["default"] == "local-dsh"
    assert config.dump()["improver"]["registry"] == str(registry)


def test_explicit_registry_wins_over_environment(monkeypatch, tmp_path):
    registry = tmp_path / "explicit.json"
    registry.write_text(json.dumps({"default": "explicit", "improvers": {}}))
    monkeypatch.setenv("HG_IMPROVER_CONFIG", str(tmp_path / "missing.json"))
    assert improver.load_config(registry)["default"] == "explicit"


def test_missing_configured_registry_does_not_fall_back(monkeypatch, tmp_path):
    monkeypatch.setenv("HG_IMPROVER_CONFIG", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        improver.load_config()


def test_cli_route_is_recorded_and_passed_only_to_method(monkeypatch, tmp_path):
    from harnessgrad.methods import _method_env
    from methods.cli_improver import _restrict_env, applies
    from harnessgrad.identity import _improver_identity

    launcher = tmp_path / "dsh"
    launcher.write_text("#!/bin/sh\nprintf 'dsh 1.2.3\\n'\n")
    launcher.chmod(0o755)
    entry = {"name": "dsh", "kind": "cli", "argv_style": "dsh",
             "launcher": str(launcher), "model": "test-model",
             "base_url": "https://gateway.invalid/v1", "provider": "local-gateway",
             "profile": "headless", "wire": "responses",
             "api_key_env": "HG_METHOD_API_KEY"}
    resolved = improver.resolve({"default": "dsh", "improvers": {"dsh": entry}})
    assert resolved["ok"]
    assert applies({"improver": resolved})
    for key in ("base_url", "provider", "profile", "wire"):
        assert resolved[key] == entry[key]
        assert _improver_identity(resolved)[key] == entry[key]
    monkeypatch.setenv("HG_AGENT_API_KEY", "task-only")
    monkeypatch.setenv("HG_METHOD_API_KEY", "method-only")
    method_env = _method_env(tmp_path, resolved)
    assert "HG_AGENT_API_KEY" not in method_env
    assert method_env["HG_METHOD_BASE_URL"] == entry["base_url"]
    assert method_env["HG_METHOD_MODEL"] == entry["model"]
    _restrict_env(method_env, _improver_identity(resolved))
    assert method_env["HG_METHOD_API_KEY"] == "method-only"
    assert "HG_AGENT_API_KEY" not in method_env
    assert "method-only" not in json.dumps(resolved)
    assert _improver_identity(resolved)["api_key_env"] == "HG_METHOD_API_KEY"
