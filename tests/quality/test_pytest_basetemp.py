"""Exercise pytest startup itself, before it can clear an unsafe basetemp."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = (
    "tests/quality/test_trials_and_unmeasured.py"
    "::test_one_trial_records_no_spread_at_all"
)


def _pytest(*args):
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", PROBE, *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("args", [
    ["--basetemp=.scratch/pytest-guard-probe"],
    ["--basetemp", str(ROOT / ".scratch" / "pytest-guard-probe")],
    [f"--basetemp={ROOT / 'tests' / '..' / '.scratch' / 'pytest-guard-probe'}"],
])
def test_in_platform_basetemp_exits_before_tests(args):
    result = _pytest(*args)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "--basetemp must be OUTSIDE the platform tree" in result.stderr
    assert "tmp_path as a harness work root" in result.stderr
    assert "--basetemp=/tmp/" in result.stderr
    assert "passed" not in result.stdout
    assert not (ROOT / ".scratch" / "pytest-guard-probe").exists()


def test_external_symlink_to_platform_is_rejected_without_clearing_it(tmp_path):
    link = tmp_path / "platform-link"
    link.symlink_to(ROOT, target_is_directory=True)
    result = _pytest(f"--basetemp={link / '.scratch' / 'pytest-guard-probe'}")
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "--basetemp must be OUTSIDE the platform tree" in result.stderr
    assert link.is_symlink()
    assert not (ROOT / ".scratch" / "pytest-guard-probe").exists()


def test_external_basetemp_runs_tests(tmp_path):
    result = _pytest("--basetemp", str(tmp_path / "external"))
    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_default_basetemp_runs_tests():
    result = _pytest()
    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    assert "1 passed" in result.stdout
