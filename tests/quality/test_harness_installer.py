"""Offline checks of version selection, npm integrity and installation publication."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

import pytest

from harnessgrad.cli import main
from tools import install_harness


def test_invalid_version_never_creates_installation(tmp_path, capsys):
    assert main(["harness", "list"]) == 0
    supported = capsys.readouterr().out
    assert "openclaw 2026.3.13" in supported
    assert "openclaw 2026.11.2" in supported
    root = tmp_path / "store"
    assert main(["harness", "install", "openclaw", "--version", "latest",
                 "--install-root", str(root)]) == 1
    assert "exact YYYY.M.D release" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("requested", [None, "2026.3.13"])
def test_install_selects_latest_verified_version_only_when_omitted(tmp_path, capsys, monkeypatch, requested):
    latest, earlier = "2026.11.2", "2026.3.13"
    releases = install_harness.openclaw.RELEASES
    monkeypatch.setattr(install_harness.openclaw, "RELEASES", {latest: releases[latest], earlier: releases[earlier]})
    expected_version = requested or latest
    prepared = tmp_path / "harness"

    def install(version, root):
        assert version == expected_version
        assert root == tmp_path
        return prepared

    monkeypatch.setattr(install_harness, "install_openclaw", install)
    args = ["harness", "install", "openclaw", "--install-root", str(tmp_path)]
    if requested is not None:
        args.extend(["--version", requested])
    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{prepared}\n"
    if requested is None:
        assert captured.err == f"Using latest verified OpenClaw version {latest} (--version omitted).\n"
    else:
        assert captured.err == ""


def test_npm_install_checks_registry_integrity_before_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setattr(install_harness.sandbox, "command", lambda argv, **kwargs: argv)
    commands = []

    def npm(argv, *, cwd, env, **kwargs):
        commands.append(argv[argv.index("--no-update-notifier") + 1:])
        assert "OPENAI_API_KEY" not in env
        assert Path(env["HOME"]).is_relative_to(tmp_path)
        (cwd / "package-lock.json").write_text(json.dumps({"packages": {
            "node_modules/openclaw": {"version": "2026.3.13", "integrity": "unreviewed"}
        }}))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(install_harness.subprocess, "run", npm)
    with pytest.raises(ValueError, match="npm lock does not match"):
        install_harness._install_package(tmp_path, tmp_path / "node", "2026.3.13",
                                         install_harness.openclaw.RELEASES["2026.3.13"])
    assert commands == [["install", "--ignore-scripts", "--package-lock-only"]]


@pytest.mark.parametrize("version", ["2026.11.2", "2026.11.3"])
def test_rebuild_is_limited_to_the_locked_package(tmp_path, monkeypatch, version):
    releases = install_harness.openclaw.RELEASES
    release = releases.get(version)
    integrity = releases["2026.11.2"].integrity
    monkeypatch.setattr(install_harness.sandbox, "command", lambda argv, **kwargs: argv)
    commands = []

    def npm(argv, *, cwd, **kwargs):
        args = argv[argv.index("--no-update-notifier") + 1:]
        commands.append(args)
        assert json.loads((cwd / "package.json").read_text())["allowScripts"] == {f"openclaw@{version}": True}
        if args[0] == "install":
            (cwd / "package-lock.json").write_text(json.dumps({"packages": {
                "node_modules/openclaw": {"version": version, "integrity": integrity,
                                           "resolved": f"https://registry.npmjs.org/openclaw/-/openclaw-{version}.tgz"}
            }}))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(install_harness.subprocess, "run", npm)
    lockfile = install_harness._install_package(tmp_path, tmp_path / "node", version, release)
    assert commands == [["install", "--ignore-scripts", "--package-lock-only"],
                        ["ci", "--ignore-scripts"],
                        ["rebuild", str(lockfile.parent / "node_modules/openclaw")]]


def test_failed_preparation_does_not_publish_an_installation(monkeypatch):
    def install_package(instance, node_root, version, release):
        project = instance / "package"
        project.mkdir()
        lockfile = project / "package-lock.json"
        lockfile.write_text("{}")
        return lockfile

    def fail(harness, launcher, *, runtime_paths, **kwargs):
        project = harness.parent / "package"
        assert runtime_paths == (project,)
        assert Path(launcher).is_relative_to(project)
        raise ValueError("version probe failed")

    monkeypatch.setattr(install_harness.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install_harness.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(install_harness, "_toolchain", lambda root: root / "node")
    monkeypatch.setattr(install_harness, "_install_package", install_package)
    monkeypatch.setattr(install_harness.openclaw, "configure", fail)
    with tempfile.TemporaryDirectory(prefix="hg-installer-test-", dir="/var/tmp") as directory:
        root = Path(directory)
        with pytest.raises(ValueError, match="version probe failed"):
            install_harness.install_openclaw("2026.3.13", root)
        assert list((root / "openclaw" / "2026.3.13").iterdir()) == []
