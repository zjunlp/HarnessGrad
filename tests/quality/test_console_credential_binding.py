"""Offline, synthetic credential-binding and loopback HTTP regressions."""
from __future__ import annotations

import http.client
import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAVED = "https://saved.invalid/v1"
OTHER = "https://other.invalid/v1"


@pytest.fixture
def console(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("credential_binding_console", ROOT / "console/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(module, "LLM_CONFIG", tmp_path / "missing.json")
    for key in ("HG_CONSOLE_TASK_BASE_URL", "HG_CONSOLE_TASK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    module.save_settings({side: {"base_url": SAVED, "api_key": f"saved-{side}"} for side in ("task", "meta")})
    return module


def form(**kwargs):
    return {"dataset": "demo", "harness": "loop", "task_model": "synthetic",
            "improver": "authored-cli", "improver_model": "editor", "rounds": 1,
            "jobs": 1, "trials": 1, **kwargs}


def test_launch_env_binds_saved_solver_key_to_exact_saved_url(console):
    def env(url, key=""):
        return console.launch_env(form(task_base_url=url, task_api_key=key))

    assert env(SAVED)["HG_AGENT_API_KEY"] == "saved-task"
    assert env(OTHER)["HG_AGENT_BASE_URL"] == OTHER
    assert env(OTHER)["HG_AGENT_API_KEY"] == ""
    assert env(OTHER, "explicit-key")["HG_AGENT_API_KEY"] == "explicit-key"
    assert env(SAVED + "/")["HG_AGENT_API_KEY"] == ""


def test_launch_env_defaults_task_but_mock_clears_task_credentials(console):
    assert console.launch_env(form())["HG_AGENT_API_KEY"] == "saved-task"
    env = console.launch_env(form(task_backend="mock", task_model="mock", task_api_key="explicit"))
    assert env["HG_AGENT_BASE_URL"] == env["HG_AGENT_API_KEY"] == ""
    assert "HG_METHOD_API_KEY" not in env


@pytest.fixture
def ready(console, tmp_path, monkeypatch):
    driver = tmp_path / "driver.py"
    driver.write_text("# synthetic\n")
    runs = tmp_path / "runs"
    budget = tmp_path / "budget"
    runs.mkdir()
    budget.mkdir()
    for name, value in {"DRIVER": driver, "RUNS": runs, "BUDGET_DIR": budget,
                        "BUDGET_CAPACITY": "1"}.items():
        monkeypatch.setattr(console, name, value)
    monkeypatch.setattr(console, "python_supported", lambda: True)
    monkeypatch.setattr(console, "sandbox_supported", lambda: SimpleNamespace(ok=True, detail="fixture"))
    monkeypatch.setattr(console, "adapters", lambda: [{"name": "demo"}])
    monkeypatch.setattr(console, "base_harnesses", lambda: ["loop"])
    monkeypatch.setattr(console, "improvers", lambda: [{"name": "authored-cli", "kind": "cli", "model": ""}])
    return console


def test_guard_pending_solver_key_is_preview_only(ready):
    launch = form(task_base_url=OTHER)
    assert ready.guard_launch(launch)[1]["error"] == "no-task-key"
    assert ready.guard_launch(launch, preview_meta_key_pending=True)[1]["error"] == "no-task-key"
    assert ready.guard_launch(launch, preview_key_pending=True) == (200, {})


def test_preview_flags_never_reveal_keys_and_post_guard_rechecks(ready):
    launch = form(task_base_url=OTHER, task_api_key="supplied-secret", task_key_present="true")
    payload = ready.launch_preview(launch)
    assert payload["preflight"]["ok"] is True
    assert "task_api_key" not in payload["form"]
    assert "supplied-secret" not in repr(payload)
    assert "saved-task" not in repr(payload) and "saved-meta" not in repr(payload)
    del launch["task_api_key"]
    assert ready.guard_launch(launch)[1]["error"] == "no-task-key"
    launch["task_key_present"] = "false"
    assert ready.launch_preview(launch)["preflight"]["error"] == "no-task-key"


@pytest.mark.parametrize("side", ["task", "meta", "assistant"])
def test_settings_reject_changed_url_without_replacement_atomically(console, side):
    console.save_settings({side: {"base_url": SAVED, "api_key": "old-secret"}})
    before = console.SETTINGS.read_bytes()
    with pytest.raises(ValueError, match="explicit API key"):
        console.save_settings({side: {"base_url": OTHER, "api_key": ""}})
    assert console.SETTINGS.read_bytes() == before
    console.save_settings({side: {"base_url": SAVED, "api_key": "", "model": "new-model"}})
    assert console.load_settings()[side]["api_key"] == "old-secret"
    console.save_settings({side: {"base_url": OTHER, "api_key": "replacement"}})
    assert console.load_settings()[side]["api_key"] == "replacement"
    assert console.load_settings()[side]["base_url"] == OTHER


@pytest.fixture
def http_console(console, monkeypatch):
    calls = []
    monkeypatch.setattr(console, "probe_endpoint", lambda url, key: calls.append((url, key)) or {"ok": True})
    server = console.ConsoleServer(("127.0.0.1", 0), console.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(server, path, payload):
    body = json.dumps(payload).encode()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        conn.request("POST", path, body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


@pytest.mark.parametrize("side", ["task", "meta"])
def test_http_settings_errors_and_explicit_probe(http_console, console, side):
    server, calls = http_console
    status, result = post(server, "/api/settings", {side: {"base_url": OTHER}})
    assert status == 400 and result["error"] == "settings-key-required"
    assert console.load_settings()[side]["base_url"] == SAVED
    status, result = post(server, "/api/settings/test", {"side": side, "base_url": OTHER})
    assert status == 400 and result["error"] == "api-key-required-for-different-base-url"
    assert not calls
    status, result = post(server, "/api/settings/test", {"side": side, "base_url": OTHER, "api_key": "new-secret"})
    assert status == 200 and result["ok"] and calls == [(OTHER, "new-secret")]
    assert "new-secret" not in repr(result)
