"""Shared method discovery reports declarations without executing methods."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harnessgrad.core.methods import describe_method, discover_methods


def test_catalog_retains_declared_requirements_and_does_not_import(tmp_path):
    root = tmp_path / "methods"
    root.mkdir()
    method = root / "skillopt"
    method.mkdir()
    (method / "method.json").write_text(json.dumps({
        "kind": "published", "requires": {"editor_model": True, "instruction_file": True},
        "note": "adapted port",
    }), encoding="utf-8")
    (method / "run.py").write_text("raise RuntimeError('must not run')", encoding="utf-8")
    result, = discover_methods(root)
    assert result.name == "skillopt"
    assert result.entrypoint == method / "run.py"
    assert result.declared_requirements == {"editor_model": True, "instruction_file": True}
    assert result.metadata["note"] == "adapted port"
    assert result.error is None
    assert not (method / "__pycache__").exists()


def test_invalid_and_missing_metadata_stay_distinct_from_empty_requirements(tmp_path):
    root = tmp_path / "methods"
    root.mkdir()
    for name, data in [("a", "{}"), ("b", "[]"), ("c", "{"), ("d", None)]:
        directory = root / name
        directory.mkdir()
        (directory / "run.py").write_text("", encoding="utf-8")
        if data is not None:
            (directory / "method.json").write_text(data, encoding="utf-8")
    entries = {m.name: m for m in discover_methods(root)}
    assert entries["a"].metadata == {} and entries["a"].error is None
    assert all(m.declared_requirements is None for m in entries.values())
    assert all(entries[k].metadata is None and entries[k].error for k in ("b", "c", "d"))


def test_catalog_excludes_private_directories_and_includes_manifest_only(tmp_path):
    root = tmp_path / "methods"
    root.mkdir()
    for name in (".private", "_helper", "manifest_only", "empty"):
        directory = root / name
        directory.mkdir()
        if name != "empty":
            (directory / "method.json").write_text('{}', encoding="utf-8")
    row, = discover_methods(root)
    assert row.name == "manifest_only"
    assert row.entrypoint is None


def test_missing_root_is_not_an_empty_catalog(tmp_path):
    with pytest.raises(ValueError, match="methods directory does not exist"):
        discover_methods(tmp_path / "missing")
    with pytest.raises(ValueError, match="method directory does not exist"):
        describe_method(tmp_path / "missing")


def test_checked_in_skillopt_declarations_are_not_workflow_readiness():
    root = Path(__file__).resolve().parents[2] / "methods"
    entries = {m.name: m for m in discover_methods(root)}
    method = entries["skillopt"]
    assert method.entrypoint.is_file()
    assert method.declared_requirements == {"editor_model": True, "instruction_file": True}
    assert "not native reproduction" in method.metadata["note"]
    assert "modular_rsi" in entries and entries["modular_rsi"].declared_requirements["min_trials"] == 2


def test_cli_lists_and_shows_methods_without_starting_them(tmp_path, capsys):
    from harnessgrad.entrypoints.method_catalogue import main

    root = tmp_path / "methods"
    root.mkdir()
    method = root / "sample"
    method.mkdir()
    (method / "method.json").write_text('{"kind":"published", "requires":{"editor_model":true}}')
    (method / "run.py").write_text("raise RuntimeError('must not run')")
    assert main(["list", "--json"], methods_root=root) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["name"] == "sample"
    assert rows[0]["metadata"]["requires"] == {"editor_model": True}
    assert main(["show", "sample"], methods_root=root) == 0
    assert json.loads(capsys.readouterr().out)["metadata"]["kind"] == "published"
    assert main(["show", "../sample"], methods_root=root) == 1
    assert "method name" in capsys.readouterr().err


def test_top_level_cli_exposes_current_method_catalog():
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-m", "harnessgrad.cli", "method", "show", "skillopt"],
                          cwd=root, env=env, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["name"] == "skillopt"
    assert data["metadata"]["requires"]["instruction_file"] is True
