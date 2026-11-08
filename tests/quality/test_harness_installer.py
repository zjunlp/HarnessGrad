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
    assert "codex 0.166.0" in supported
    assert "codex 0.167.0" in supported
    root = tmp_path / "store"
    assert main(["harness", "install", "openclaw", "--version", "latest",
                 "--install-root", str(root)]) == 1
    assert "exact YYYY.M.D release" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("name, latest, earlier, label", [
    ("openclaw", "2026.11.2", "2026.3.13", "OpenClaw"),
    ("codex", "0.167.0", "0.166.0", "Codex"),
    ("opencode", "2.2.6", "1.19.5", "OpenCode"),
])
@pytest.mark.parametrize("explicit", [False, True])
def test_install_selects_latest_verified_version_only_when_omitted(tmp_path, capsys, monkeypatch,
                                                                name, latest, earlier, label, explicit):
    owner, key = (install_harness.openclaw, "RELEASES") if name == "openclaw" else (
        install_harness, f"{name.upper()}_RELEASES")
    releases = getattr(owner, key)
    monkeypatch.setattr(owner, key, {latest: releases[latest], earlier: releases[earlier]})
    prepared = tmp_path / "harness"

    def install(harness, version, root):
        assert harness == name
        assert version == (earlier if explicit else latest)
        assert root == tmp_path
        return prepared

    monkeypatch.setattr(install_harness, "install_harness", install)
    args = ["harness", "install", name, "--install-root", str(tmp_path)]
    if explicit:
        args.extend(["--version", earlier])
    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{prepared}\n"
    if not explicit:
        assert captured.err == f"Using latest verified {label} version {latest} (--version omitted).\n"
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
        install_harness._install_package(tmp_path, tmp_path / "node",
                                         install_harness._recipe("openclaw", "2026.3.13"))
    assert commands == [["install", "--ignore-scripts", "--package-lock-only"]]


@pytest.mark.parametrize("version", ["2026.11.2", "2026.11.3"])
def test_rebuild_is_limited_to_the_locked_package(tmp_path, monkeypatch, version):
    releases = install_harness.openclaw.RELEASES
    release = install_harness._recipe("openclaw", version)
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
    lockfile = install_harness._install_package(tmp_path, tmp_path / "node", release)
    assert commands == [["install", "--ignore-scripts", "--package-lock-only"],
                        ["ci", "--ignore-scripts"],
                        ["rebuild", str(lockfile.parent / "node_modules/openclaw")]]


@pytest.mark.parametrize("valid_native", [False, True])
def test_codex_npm_checks_linux_payload_before_installing(tmp_path, monkeypatch, valid_native):
    release = install_harness._recipe("codex", "0.167.0")
    monkeypatch.setattr(install_harness.sandbox, "command", lambda argv, **kwargs: argv)
    commands = []

    def npm(argv, *, cwd, **kwargs):
        args = argv[argv.index("--no-update-notifier") + 1:]
        commands.append(args)
        assert json.loads((cwd / "package.json").read_text()) == {
            "private": True, "dependencies": {"@openai/codex": "0.167.0"},
        }
        if args[0] == "install":
            (cwd / "package-lock.json").write_text(json.dumps({"packages": {
                "node_modules/@openai/codex": {"version": "0.167.0", "integrity": release.integrity},
                "node_modules/@openai/codex-linux-x64": {
                    "version": "0.167.0-linux-x64",
                    "integrity": release.native_integrity if valid_native else "wrong payload",
                },
            }}))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(install_harness.subprocess, "run", npm)
    expected = [["install", "--ignore-scripts", "--package-lock-only"]]
    if valid_native:
        install_harness._install_package(tmp_path, tmp_path / "node", release)
        expected.append(["ci", "--ignore-scripts"])
    else:
        with pytest.raises(ValueError, match="codex@0.167.0-linux-x64"):
            install_harness._install_package(tmp_path, tmp_path / "node", release)
    assert commands == expected


def test_failed_preparation_does_not_publish_an_installation(monkeypatch):
    def install_package(instance, node_root, release):
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
            install_harness.install_harness("openclaw", "2026.3.13", root)
        assert list((root / "openclaw" / "2026.3.13").iterdir()) == []
