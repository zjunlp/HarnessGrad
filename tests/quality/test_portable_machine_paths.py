"""Portable test scratch selection, without launching the quality suite."""
import importlib.util
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("portable_run_tests", ROOT / "tools/run_tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("HG_TEST_TMPDIR", raising=False)
    return module


def test_standard_tempdir(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    assert runner.default_basetemp_parent() == str(tmp_path)


def test_tmpdir_environment(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(runner.tempfile, "tempdir", None)
    assert runner.default_basetemp_parent() == str(tmp_path)


def test_explicit_override_wins(runner, monkeypatch, tmp_path):
    override = tmp_path / "new-scratch"
    monkeypatch.setenv("HG_TEST_TMPDIR", str(override))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    assert runner.default_basetemp_parent() == str(override)
    assert not override.exists()  # Selection does not write before validation.


@pytest.mark.parametrize("source", ["cli", "override", "standard"])
@pytest.mark.parametrize("target", ["root", "child", "symlink"])
def test_refuse_checkout_temp_roots(runner, monkeypatch, tmp_path, capsys, source, target):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(runner, "ROOT", checkout)
    path = checkout if target == "root" else checkout / "not-created"
    if target == "symlink":
        path = tmp_path / "link"
        path.symlink_to(checkout, target_is_directory=True)
    monkeypatch.setattr(runner, "discover", lambda _: [checkout / "tests/test_example.py"])
    monkeypatch.setattr(runner, "load_costs", lambda: {})
    argv = ["run_tests.py", "--list"]
    if source == "cli":
        argv += ["--basetemp-root", str(path)]
    elif source == "override":
        monkeypatch.setenv("HG_TEST_TMPDIR", str(path))
    else:
        monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(path))
    monkeypatch.setattr(sys, "argv", argv)
    assert runner.main() == 2
    assert "inside the platform" in capsys.readouterr().err
    assert list(checkout.iterdir()) == []


@pytest.mark.parametrize("explicit_cli", [False, True])
def test_external_root_created(runner, monkeypatch, tmp_path, capsys, explicit_cli):
    parent = tmp_path / "external-scratch"
    monkeypatch.setenv("HG_TEST_TMPDIR", str(parent))
    monkeypatch.setattr(runner, "discover", lambda _: [ROOT / "tests/test_example.py"])
    monkeypatch.setattr(runner, "load_costs", lambda: {})
    argv = ["run_tests.py", "--list"]
    if explicit_cli:
        argv += ["--basetemp-root", str(parent / "cli")]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        assert runner.main() == 0
        output = capsys.readouterr().out
        if explicit_cli:
            assert f"basetemp: {parent / 'cli'} " in output
        else:
            assert f"basetemp: {parent}/harnessgrad-pytest-" in output
            assert len(list(parent.iterdir())) == 1
    finally:
        shutil.rmtree(parent, ignore_errors=True)
