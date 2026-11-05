"""Console paths are checkout-local code plus explicitly selected external receipts."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    spec = importlib.util.spec_from_file_location("portable_console_test", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def portable_env(monkeypatch, tmp_path):
    monkeypatch.delenv("HG_CONSOLE_RUNS", raising=False)
    monkeypatch.delenv("HG_CONSOLE_PLATFORM", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return tmp_path / "home" / ".local/share/harnessgrad/runs"


def test_defaults_follow_module_checkout_and_user_home(portable_env):
    server = load("console/server.py")
    smoke = load("console/uat/smoke.py")
    assert server.PLATFORM == smoke.PLATFORM == ROOT
    assert server.RUNS == smoke.DEFAULT_RUNS == portable_env
    assert server.BASE_HARNESS == ROOT / "base_harness/loop"
    assert not portable_env.exists(), "import must not manufacture receipt storage"


@pytest.mark.parametrize("runs", ["relative receipts", "/external/receipts"])
def test_explicit_environment_paths_are_preserved(monkeypatch, portable_env, runs):
    monkeypatch.setenv("HG_CONSOLE_RUNS", runs)
    monkeypatch.setenv("HG_CONSOLE_PLATFORM", "/external/platform")
    server = load("console/server.py")
    assert server.RUNS == Path(runs)
    assert server.PLATFORM == Path("/external/platform")


def test_missing_default_fails_before_server_creation(monkeypatch, portable_env):
    server = load("console/server.py")
    monkeypatch.setattr(sys, "argv", ["server.py"])
    def forbidden(*args, **kwargs):
        pytest.fail("missing receipts must not start a server")
    monkeypatch.setattr(server, "ConsoleServer", forbidden)
    with pytest.raises(SystemExit, match="HG_CONSOLE_RUNS"):
        server.main()
    assert not portable_env.exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_uat_cli_overrides_env_without_starting_server(monkeypatch, portable_env, tmp_path, capsys, explicit):
    env_root = tmp_path / "env-receipts"
    cli_root = tmp_path / "cli-receipts"
    monkeypatch.setenv("HG_CONSOLE_RUNS", str(env_root))
    smoke = load("console/uat/smoke.py")
    monkeypatch.setattr(smoke, "port_free", lambda port: True)
    args = ["--runs-root", str(cli_root)] if explicit else []
    assert smoke.main(args) == 2
    assert str(cli_root if explicit else env_root) in capsys.readouterr().err
    assert not cli_root.exists() and not env_root.exists()
