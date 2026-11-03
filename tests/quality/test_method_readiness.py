"""Method configuration is checked before a run measures its baseline."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shlex
import sys
from unittest.mock import patch

import pytest

import driver
from harnessgrad import readiness
from harnessgrad.methods import _check_method_entrypoint


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fixture_catalogue(monkeypatch):
    # These tests exercise methods; importing every benchmark adds unrelated dependencies.
    monkeypatch.setattr(readiness.datasets, "published", lambda: ())
    for key in ("HG_IMPROVER", "HG_IMPROVER_NAME"):
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
    return next((row for row in readiness.configuration_checks(args) if row.name == "method"), None)


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
    rows = readiness.configuration_checks(arguments(tmp_path, entry))
    assert all(row.ok for row in rows), rows
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "work").exists()


def test_missing_method_cannot_pass_readiness(tmp_path):
    entry = shlex.join([sys.executable, str(tmp_path / "absent.py")])
    row = method_check(arguments(tmp_path, entry))
    assert row is not None
    assert row.required and not row.ok


def test_shared_editor_method_requires_its_model_configuration(tmp_path, monkeypatch):
    for key in ("HG_METHOD_BASE_URL", "HG_METHOD_MODEL", "HG_METHOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    entry = shlex.join([sys.executable, str(ROOT / "methods" / "dgm" / "run.py")])
    rows = readiness.plan_checks(arguments(tmp_path, entry))
    editor = next((row for row in rows if row.name == "method"), None)
    assert editor is not None
    assert editor.required and not editor.ok
    assert "HG_METHOD_" in editor.detail


def test_noop_does_not_require_an_editor(tmp_path, monkeypatch):
    for key in ("HG_METHOD_BASE_URL", "HG_METHOD_MODEL", "HG_METHOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    entry = shlex.join([sys.executable, str(ROOT / "methods" / "noop" / "run.py")])
    rows = readiness.plan_checks(arguments(tmp_path, entry))
    assert not [row for row in rows if row.required and not row.ok]
    assert readiness.configuration_checks(arguments(tmp_path, entry)) == []


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


def test_cli_override_needs_no_method_endpoint(tmp_path, monkeypatch):
    for key in ("HG_METHOD_BASE_URL", "HG_METHOD_MODEL", "HG_METHOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HG_IMPROVER", shlex.join([sys.executable, "--profile", "custom"]))
    entry = shlex.join([sys.executable, str(ROOT / "methods" / "dgm" / "run.py")])
    rows = readiness.configuration_checks(arguments(tmp_path, entry))
    editor = next(row for row in rows if row.name == "method")
    assert editor.ok, editor.detail


def test_missing_cli_override_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_IMPROVER", "no-such-harnessgrad-editor --profile custom")
    entry = shlex.join([sys.executable, str(ROOT / "methods" / "dgm" / "run.py")])
    editor = next(row for row in readiness.configuration_checks(arguments(tmp_path, entry))
                  if row.name == "method")
    assert not editor.ok
    assert "no-such-harnessgrad-editor" in editor.detail


def test_cli_override_preserves_structured_role_endpoint_requirement(tmp_path, monkeypatch):
    for key in ("HG_METHOD_BASE_URL", "HG_METHOD_MODEL", "HG_METHOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HG_IMPROVER", shlex.join([sys.executable, "--profile", "custom"]))
    entry = shlex.join([sys.executable, str(ROOT / "methods" / "rrsi" / "run.py")])
    rows = readiness.configuration_checks(arguments(tmp_path, entry))
    assert any(not row.ok and "structured roles" in row.detail for row in rows)


@pytest.mark.parametrize("name", ["a", "b"])
def test_reference_editor_methods_require_configuration(tmp_path, monkeypatch, name):
    for key in ("HG_METHOD_BASE_URL", "HG_METHOD_MODEL", "HG_METHOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    entry = shlex.join([sys.executable, str(ROOT / "methods" / name / "run.py")])
    rows = readiness.configuration_checks(arguments(tmp_path, entry))
    assert any(row.required and not row.ok and "HG_METHOD_" in row.detail for row in rows)


@pytest.mark.parametrize("name", ["a", "b", "dgm"])
def test_cli_editor_does_not_need_endpoint_sdk(tmp_path, monkeypatch, name):
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: None)
    monkeypatch.setenv("HG_IMPROVER", shlex.join([sys.executable, "--profile", "custom"]))
    entry = shlex.join([sys.executable, str(ROOT / "methods" / name / "run.py")])
    rows = readiness.configuration_checks(arguments(tmp_path, entry))
    assert all(row.ok for row in rows), rows


def test_endpoint_editor_requires_installed_sdk(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: None)
    for key, value in (("BASE_URL", "http://127.0.0.1:1/v1"),
                       ("MODEL", "fixture-editor"), ("API_KEY", "local")):
        monkeypatch.setenv("HG_METHOD_" + key, value)
    entry = shlex.join([sys.executable, str(ROOT / "methods" / "dgm" / "run.py")])
    rows = readiness.configuration_checks(arguments(tmp_path, entry))
    assert any(row.required and not row.ok and "openai package" in row.detail for row in rows)


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
