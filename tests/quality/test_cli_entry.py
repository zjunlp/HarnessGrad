"""The installed command: one entrypoint, two spellings, no new dependencies.

`python3 driver.py` and `harnessgrad run` have to be the same program. These tests pin the
packaging contract (stdlib-only, versioned, one console script) and the delegation (`run`
forwards its arguments to the real driver). Every invocation ends in help, a version or a
refusal: no test starts a study, binds a model endpoint or touches the network.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "driver.py"


def _cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a child process from the repository root.

    A child, not an in-process call, because `python3 -m harnessgrad.cli` is the form the
    contract promises; `cwd=ROOT` is what makes the checkout importable without the
    package being installed. `PYTHONPATH` is cleared so a preinstalled HarnessGrad cannot
    answer for the tree under test, and bytecode writes are off for the same reason the
    quality conftest turns them off: this test must not write into the tree it checks.
    """
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-m", "harnessgrad.cli", *arguments],
        cwd=ROOT, env=environment, text=True, timeout=120,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _driver_flags() -> list[str]:
    """The long options `driver.py` declares, read from its source, never hardcoded."""
    source = DRIVER.read_text(encoding="utf-8")
    return re.findall(r"""add_argument\(\s*["'](--[a-z][a-z0-9-]*)["']""", source)


def test_pyproject_is_the_installable_contract():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream)
    project = config["project"]
    assert project["name"] == "harnessgrad"
    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.12"
    # The selling point, as an assertion: installing adds nothing to resolve.
    assert project["dependencies"] == []
    assert project["scripts"]["harnessgrad"] == "harnessgrad.cli:main"
    license_ = project["license"]
    assert (license_["text"] if isinstance(license_, dict) else license_).upper() == "MIT"
    assert (ROOT / "LICENSE").is_file()

    # Discovery is part of the contract: the driver must stay importable under its
    # documented name, the directories that deliberately have no `__init__.py` must be
    # found as namespaces rather than force a new import graph, and the generated working
    # state under `data/` must not be installed as packages.
    setuptools = config["tool"]["setuptools"]
    assert setuptools["py-modules"] == ["driver"]
    finder = setuptools["packages"]["find"]
    assert finder["namespaces"] is True
    for pattern in ("harnessgrad*", "eval*", "data*", "methods*", "tools*"):
        assert pattern in finder["include"]
    assert {"data.authored*", "data.candidates*"} <= set(finder["exclude"])


def test_module_version_prints_and_exits_zero():
    result = _cli("--version")
    assert result.returncode == 0, result.stderr
    assert re.search(r"\b\d+\.\d+(?:\.\d+)?\b", result.stdout), result.stdout


def test_run_help_is_the_drivers_own_help():
    declared = _driver_flags()
    assert declared, "driver.py declares no long options; the source lookup is broken"
    result = _cli("run", "--help")
    assert result.returncode == 0, result.stderr
    assert "usage: driver.py" in result.stdout
    found = [flag for flag in declared if flag in result.stdout]
    assert found, ("driver help names none of driver.py's declared options: "
                   f"{sorted(set(declared))}")


def test_run_does_not_forward_the_cli_separator():
    # `--` marks where this command's line ends; forwarding it would make the driver
    # treat `--help` as a positional argument and refuse a command the user asked for.
    result = _cli("run", "--", "--help")
    assert result.returncode == 0, result.stderr
    assert "usage: driver.py" in result.stdout


def test_main_returns_an_int_without_raising():
    from harnessgrad.cli import main
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = main(["run", "--help"])
    assert isinstance(status, int) and not isinstance(status, bool)
    assert status == 0
    assert "usage: driver.py" in output.getvalue()


def test_unknown_command_exits_non_zero_and_is_named():
    result = _cli("frobnicate")
    assert result.returncode != 0
    assert "frobnicate" in result.stderr
