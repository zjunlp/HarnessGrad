"""A host with python3 but no python alias can launch a files harness."""
import sys

from eval.run import runner
from eval.containers import sandbox


def test_existing_python_path_selection_is_preserved(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/local/bin/python")
    assert runner._files_python() == "python"


def test_missing_python_alias_uses_the_running_interpreter(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert runner._files_python() == sys.executable


def test_symlinked_virtualenv_keeps_its_own_runtime_prefix(tmp_path, monkeypatch):
    prefix = tmp_path / "venv"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    executable = prefix / "bin" / "python3"
    executable.symlink_to(sys.executable)
    monkeypatch.setattr(sandbox.sys, "executable", str(executable))
    monkeypatch.setattr(sandbox.sys, "prefix", str(prefix))
    monkeypatch.setattr(sandbox.sys, "base_prefix", "/usr")
    assert sandbox.interpreter_prefix() == prefix
