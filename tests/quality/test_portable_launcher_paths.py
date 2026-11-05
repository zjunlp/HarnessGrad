"""Portable launcher resolution without model, network, or AppWorld execution."""
import os
from pathlib import Path
import subprocess

import pytest

import ast
import shutil
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]

# Importing the dataset prepares shared data. Compile only the self-contained
# resolver so these unit tests cannot load, download, or mutate any dataset.
_tree = ast.parse((ROOT / "data/datasets/appworld/control.py").read_text())
_resolver = next(node for node in _tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_aw_python")
_namespace = {"os": os, "shutil": shutil}
exec(compile(ast.Module(body=[_resolver], type_ignores=[]), "control.py", "exec"), _namespace)
control = SimpleNamespace(_aw_python=_namespace["_aw_python"])


def executable(path, body="exit 0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("value", [None, "", "/nonexistent/appworld/python"])
def test_appworld_requires_explicit_runtime(monkeypatch, value):
    monkeypatch.delenv("AW_PYTHON", raising=False)
    if value is not None:
        monkeypatch.setenv("AW_PYTHON", value)
    with pytest.raises(SystemExit, match="Set AW_PYTHON.*AppWorld"):
        control._aw_python()


@pytest.mark.parametrize("kind", ["absolute", "relative", "command", "home", "symlink"])
def test_appworld_explicit_executable(tmp_path, monkeypatch, kind):
    python = executable(tmp_path / "bin/python-aw", "printf 'fake-runtime\\n'")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(python.parent))
    monkeypatch.setenv("HOME", str(tmp_path))
    link = tmp_path / "venv/bin/python"
    link.parent.mkdir(parents=True)
    link.symlink_to(python)
    values = {
        "absolute": str(python), "relative": "bin/python-aw", "command": "python-aw",
        "home": "~/bin/python-aw", "symlink": str(link),
    }
    monkeypatch.setenv("AW_PYTHON", values[kind])
    selected = control._aw_python()
    assert selected == str(link if kind == "symlink" else python)
    result = subprocess.run([selected], cwd="/", capture_output=True, text=True, check=True)
    assert result.stdout == "fake-runtime\n"


def test_appworld_nonexecutable_override(tmp_path, monkeypatch):
    candidate = tmp_path / "python"
    candidate.write_text("not executable")
    monkeypatch.setenv("AW_PYTHON", str(candidate))
    with pytest.raises(SystemExit, match="AW_PYTHON"):
        control._aw_python()
