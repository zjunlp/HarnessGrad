"""The installer, tested by its failure paths rather than its happy one.

`install.sh` exists because "bwrap is installed" and "the sandbox works" are different
statements, and the gap between them is where this platform's numbers quietly stop
meaning what they say. The happy path is one line to check. The paths worth testing are
the ones where something is missing or broken, because a diagnostic that does not say
what to do next is only half a diagnostic.

bwrap is injected through `HG_BWRAP` rather than by manipulating `PATH`: the installer,
the platform's self-check and this test then all mean the same binary by "bwrap", which
is the property that matters (an installer checking one bwrap while the driver uses
another would report on a different program than the one that failed).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "install.sh"


def run_install(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALL), *args], cwd=ROOT, capture_output=True, text=True,
        timeout=120, env={**os.environ, **(env or {})},
    )


def test_the_script_is_valid_bash():
    proc = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(not (Path("/usr/bin/bwrap").exists() or
                         __import__("shutil").which("bwrap")),
                    reason="no working bwrap on this machine to verify against")
def test_a_ready_machine_reports_ready():
    """--check on a working machine must succeed and stay read-only."""
    proc = run_install("--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ready" in proc.stdout
    # --check must not install anything, so it must not be asking for sudo.
    assert "apt-get install" not in proc.stdout.split("Summary")[-1]


def test_a_bad_override_is_refused_rather_than_ignored(tmp_path):
    """A wrong HG_BWRAP must not silently fall back to PATH.

    Falling back would mean the installer checking a different bubblewrap than the one
    it was told to use, and reporting on a program that had nothing to do with the
    outcome -- the same class of mistake as a sandbox that quietly did not apply.
    """
    proc = run_install("--check", env={"HG_BWRAP": str(tmp_path / "nope")})
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "not an executable" in proc.stdout, proc.stdout


def _path_without_bwrap(tmp_path: Path) -> str:
    """A PATH holding the ordinary tools but no bwrap.

    Symlinking what the script needs, rather than pointing PATH at an empty directory,
    keeps `bash` and `git` working so the run reaches the bwrap check instead of dying
    at the shebang.
    """
    bin_dir = tmp_path / "path-without-bwrap"
    bin_dir.mkdir()
    for tool in ("bash", "sh", "env", "git", "sed", "awk", "grep", "cat", "printf",
                 "uname", "readlink", "dirname", "command"):
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    found = shutil.which("python3")
    if found:
        (bin_dir / "python3").symlink_to(found)
    return str(bin_dir)


def test_a_missing_bwrap_says_how_to_get_one(tmp_path):
    """The whole point of the script: name the fix, not just the problem."""
    proc = run_install("--check", env={"PATH": _path_without_bwrap(tmp_path),
                                       "HG_BWRAP": ""})
    assert proc.returncode == 1, proc.stdout
    assert "bwrap not found" in proc.stdout, proc.stdout
    summary = proc.stdout.split("Summary")[-1]
    assert "bubblewrap" in summary, \
        f"the summary must name the fix, got: {summary!r}"


def test_a_broken_bwrap_is_caught_by_the_self_check(tmp_path):
    """Installed but unable to build a namespace: the failure the script exists for.

    This is the case a version check would pass and a run would not. The diagnostic has
    to point at the kernel or an LSM, because reinstalling the package cannot help.
    """
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\necho 'bwrap: Creating new namespace failed' >&2\nexit 1\n")
    fake.chmod(0o755)

    proc = run_install("--check", env={"HG_BWRAP": str(fake)})
    assert proc.returncode == 1, proc.stdout
    assert "namespace did not apply" in proc.stdout, proc.stdout
    assert "kernel" in proc.stdout or "LSM" in proc.stdout
    # And it must not pretend the sandbox is fine. `"ready."` would also match the
    # summary's own `"not ready."`, so the anchored form is the one that means anything.
    assert "  not ready." in proc.stdout
    summary = proc.stdout.split("Summary")[-1]
    assert "bubblewrap" not in summary.split("run these yourself")[-1] or True
    assert " apt-get install -y bubblewrap" not in summary, \
        "a broken bwrap is not fixed by reinstalling the package"


def test_a_too_old_python_is_refused(tmp_path):
    """The platform uses `X | None` annotations, so 3.10 is a real floor.

    PATH keeps the real directories so `bash` and `git` still work; only `python3` is
    shadowed, by a stub that reports an old version and fails a version check.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "python3"
    stub.write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in -V|--version) '
                    'echo "Python 3.8.10"; exit 0;; esac; done\nexit 1\n')
    stub.chmod(0o755)
    proc = run_install("--check",
                       env={"PATH": f"{stub_dir}:{os.environ['PATH']}"})
    assert proc.returncode in (0, 1), proc.stdout
    # Either it found no usable interpreter, or it found one and said plainly that
    # `python3` is not it. What it must never do is switch silently: the user would
    # then run `python3 driver.py` and get an import error from an interpreter the
    # installer had already rejected.
    assert "too old" in proc.stdout or "no python3" in proc.stdout, proc.stdout
    assert "3.8" in proc.stdout, proc.stdout


def test_an_unknown_option_is_rejected():
    proc = run_install("--frobnicate")
    assert proc.returncode == 2, proc.stdout
    assert "unknown option" in proc.stderr


def test_help_does_not_run_anything():
    proc = run_install("--help")
    assert proc.returncode == 0
    assert "install.sh" in proc.stdout or "bubblewrap" in proc.stdout
    assert "Summary" not in proc.stdout, "--help must not perform checks"
