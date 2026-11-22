"""Method configuration is checked before a run measures its baseline."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from unittest.mock import patch

import pytest

import driver
from harnessgrad.orchestration import readiness
from harnessgrad.execution.method_runner import _check_method_entrypoint


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fixture_catalogue(monkeypatch):
    # These tests exercise methods; importing every benchmark adds unrelated dependencies.
    monkeypatch.setattr(readiness.datasets, "published", lambda: ())
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    for key in ("HG_IMPROVER", "HG_IMPROVER_NAME", "HG_IMPROVER_CONFIG",
                "HG_METHOD_BASE_URL", "HG_METHOD_MODEL", "HG_METHOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def arguments(tmp_path, method, **overrides):
    values = dict(dataset="demo", harness="loop", harness_path=None, side="train",
                  split=None, sampling="all", rounds=1, trials=1, jobs=1,
                  sandbox=False, check_net=False, improver="none",
                  improver_model=None, method_entrypoint=method,
                  runs_root=str(tmp_path / "runs"), work_root=str(tmp_path / "work"))
    values.update(overrides)
    return argparse.Namespace(**values)


def method_check(args):
    return next((row for row in readiness.plan_checks(args) if row.name == "method"), None)


def test_missing_absolute_method_is_rejected(tmp_path):
    missing = str(tmp_path / "missing.py")
    problem = _check_method_entrypoint([sys.executable, missing])
    assert problem is not None
    assert missing in problem


def test_quoted_external_method_with_spaces_is_ready(tmp_path):
    script = tmp_path / "my method" / "run.py"
    script.parent.mkdir()
    script.write_text("print('{}')\n", encoding="utf-8")
    entry = shlex.join([sys.executable, str(script)])
    row = method_check(arguments(tmp_path, entry))
    assert row is not None
    assert row.ok, row.detail
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "work").exists()


def test_missing_method_cannot_pass_readiness(tmp_path):
    entry = shlex.join([sys.executable, str(tmp_path / "absent.py")])
    row = method_check(arguments(tmp_path, entry))
    assert row is not None
    assert row.required and not row.ok


@pytest.fixture
def registered_cli(tmp_path, monkeypatch):
    launcher = tmp_path / "authored-cli"
    launcher.write_text("#!/bin/sh\nprintf 'authored-cli 1.2.3\\n'\n", encoding="utf-8")
    launcher.chmod(0o755)
    registry = tmp_path / "improvers.json"
    registry.write_text(json.dumps({"default": "authored", "improvers": {
        "authored": {"name": "authored", "kind": "cli", "argv_style": "dsh",
                     "launcher": str(launcher), "model": "authored-model",
                     "provider": "authored", "profile": "headless"},
        "http-editor": {"name": "http-editor", "kind": "openai-compatible",
                        "model": "authored-model", "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": "HG_METHOD_API_KEY"},
    }}))
    monkeypatch.setenv("HG_IMPROVER_CONFIG", str(registry))
    return launcher


@pytest.mark.parametrize("selector", ["none", "http-editor", "missing-editor"])
def test_model_driven_method_refuses_endpoint_as_editor_before_h0(
        tmp_path, monkeypatch, registered_cli, selector):
    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_METHOD_MODEL", "authored-model")
    monkeypatch.setenv("HG_METHOD_API_KEY", "authored-key")
    if selector == "none":
        monkeypatch.setenv("HG_IMPROVER", str(registered_cli))

    entry = shlex.join([sys.executable, str(ROOT / "methods/dgm/run.py")])
    args = arguments(tmp_path, entry, improver=selector)
    for checks in (readiness.configuration_checks, readiness.method_checks):
        rows = checks(args)
        assert any(row.name == "editor" and row.required and not row.ok for row in rows)
    monkeypatch.setattr(driver, "evaluate", lambda *a, **k: pytest.fail("H0 was executed"))
    monkeypatch.setattr(sys, "argv", ["driver.py", "--harness", "loop_plain", "--dataset", "demo",
                                    "--sampling", "all", "--rounds", "1", "--method-entrypoint", entry,
                                    "--improver", selector, "--runs-root", str(tmp_path / "runs"),
                                    "--work-root", str(tmp_path / "work"), "--no-sandbox"])
    assert driver.main() == 2
    assert not list((tmp_path / "runs").glob("*/curve.jsonl"))


def test_noop_does_not_require_an_editor(tmp_path, monkeypatch):
    for key in ("HG_METHOD_BASE_URL", "HG_METHOD_MODEL", "HG_METHOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    entry = shlex.join([sys.executable, str(ROOT / "methods" / "noop" / "run.py")])
    rows = readiness.plan_checks(arguments(tmp_path, entry))
    assert not [row for row in rows if row.name == "editor" and row.required and not row.ok]
    row = method_check(arguments(tmp_path, entry))
    assert row is not None and row.ok


def test_env_template_numeric_agent_options_are_usable():
    with patch.dict(os.environ):
        for key in tuple(os.environ):
            if key.startswith("HG_"):
                del os.environ[key]
        driver.load_env(ROOT / ".env.example")
        assert float(os.environ["HG_AGENT_TIMEOUT_S"]) == 180
        assert int(os.environ["HG_AGENT_RETRIES"]) == 3


@pytest.mark.parametrize("sampling", ["diagnostic", "pilot_10pct"])
def test_check_only_uses_the_staged_rounds_default(tmp_path, monkeypatch, sampling):
    monkeypatch.setattr(sys, "argv", ["driver.py", "--dataset", "demo", "--sampling", sampling,
                                    "--check-only", "--no-sandbox",
                                    "--runs-root", str(tmp_path / "runs"),
                                    "--work-root", str(tmp_path / "work")])
    assert driver.main() == 0
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("method", ["dgm", "rrsi", "meta_harness"])
def test_registered_cli_supports_edit_and_structured_roles_without_method_endpoint(
        tmp_path, registered_cli, method):
    entry = shlex.join([sys.executable, str(ROOT / "methods" / method / "run.py")])
    args = arguments(tmp_path, entry, improver="authored", rounds=2)
    for checks in (readiness.configuration_checks, readiness.method_checks):
        rows = checks(args)
        assert all(row.ok for row in rows if row.required), rows


@pytest.mark.parametrize("executable", ["missing-editor", sys.executable])
def test_ambient_cli_cannot_replace_registered_editor(tmp_path, monkeypatch, registered_cli, executable):
    monkeypatch.setenv("HG_IMPROVER", executable)
    entry = shlex.join([sys.executable, str(ROOT / "methods/dgm/run.py")])
    args = arguments(tmp_path, entry, improver="authored")
    for checks in (readiness.configuration_checks, readiness.method_checks):
        rows = checks(args)
        assert any(row.name == "editor" and not row.ok for row in rows)


def test_missing_method_stops_before_dataset_loading(tmp_path, monkeypatch):
    def unexpected_load(*args, **kwargs):
        pytest.fail("a missing method must be rejected before loading or measuring tasks")

    monkeypatch.setattr(driver.datasets, "load_split", unexpected_load)
    entry = shlex.join([sys.executable, str(tmp_path / "absent.py")])
    monkeypatch.setattr(sys, "argv", ["driver.py", "--dataset", "demo", "--sampling", "all",
                                    "--rounds", "1", "--method-entrypoint", entry,
                                    "--improver", "none", "--runs-root", str(tmp_path / "runs"),
                                    "--work-root", str(tmp_path / "work"), "--no-sandbox"])
    assert driver.main() == 2
    assert not (tmp_path / "runs").exists()
