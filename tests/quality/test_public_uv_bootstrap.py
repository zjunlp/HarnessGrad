"""Offline checks for the public bootstrap and installer diagnostics."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[2]


def test_readme_uv_bootstrap_contract(tmp_path):
    text = (ROOT / "README.md").read_text()
    block = text.split("```bash", 1)[1].split("```", 1)[0]
    commands = [shlex.split(line) for line in block.splitlines() if line.strip()]
    create = next(command for command in commands if command[:2] == ["uv", "venv"])
    assert create == ["uv", "venv", "--seed", "--python", "3.12", "~/.venvs/harnessgrad"]
    assert ["uv", "pip", "install", "-e", "."] in commands
    assert "offline setup requires an appropriate pre-populated" in text
    assert "may download" in text
    # A fake uv records the actual documented arguments without network access.
    fake = tmp_path / "uv"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$UV_ARGS"\n')
    fake.chmod(0o755)
    env = dict(os.environ, UV_ARGS=str(tmp_path / "args"))
    subprocess.run([str(fake), *create[1:]], env=env, check=True)
    assert (tmp_path / "args").read_text().splitlines() == create[1:]


def test_install_check_reports_tmp_venv_boundary(tmp_path):
    if sys.version_info < (3, 12):
        import pytest
        pytest.skip("installer requires Python 3.12+")
    venv.EnvBuilder(with_pip=False).create(tmp_path / "venv")
    env = dict(os.environ, PATH=f"{tmp_path / 'venv/bin'}:{os.environ['PATH']}",
               HG_BWRAP="/bin/true")
    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--check"],
                            cwd=ROOT, env=env, text=True, capture_output=True, timeout=60)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "under a hidden sandbox tree" in output
    assert "create the venv outside /tmp" in output
    assert "the cause is the kernel or an LSM" not in output
    assert "sandbox self-check failed" in output
