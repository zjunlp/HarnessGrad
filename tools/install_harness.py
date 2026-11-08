"""Install base harness releases into an experiment runtime store."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

from eval.containers import sandbox
from harnessgrad.paths import main_checkout
from harnessgrad.python_artifacts import atomic_json, digest, locked
from tools import configure_openclaw_harness as openclaw

NODE_VERSION = "24.22.2"
NPM_VERSION = "11.19.1"
NODE_DIST = f"node-v{NODE_VERSION}-linux-x64"
NODE_SHA256 = "48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75"
TEMPLATE = openclaw.TEMPLATE


def default_root() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data_home / "harnessgrad" / "base-harnesses"


def _npm_argv(node_root: Path) -> list[str]:
    return [str(node_root / "bin/node"), str(node_root / "lib/node_modules/npm/bin/npm-cli.js")]


def _check_toolchain(node_root: Path) -> None:
    npm = _npm_argv(node_root)
    with tempfile.TemporaryDirectory(prefix="hg-node-version-") as home:
        env = {"PATH": os.defpath, "HOME": home, "NODE_DISABLE_COMPILE_CACHE": "1"}
        for argv, version in ((npm[:1], f"v{NODE_VERSION}"), (npm, NPM_VERSION)):
            actual = subprocess.check_output([*argv, "--version"], cwd=home, env=env,
                                             text=True, timeout=20).strip()
            if actual != version:
                raise ValueError(f"{Path(argv[-1]).name} version mismatch: expected {version}, found {actual}")


def _toolchain(root: Path) -> Path:
    target = root / NODE_DIST
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise ValueError(f"Node installation must be an owned directory: {target}")
        node, npm = map(Path, _npm_argv(target))
        receipt = json.loads((target / ".harnessgrad-node.json").read_text())
        if receipt != {"archive_sha256": NODE_SHA256, "node_sha256": digest(node), "npm_sha256": digest(npm)}:
            raise ValueError(f"installed Node executable or npm launcher changed: {target}")
        _check_toolchain(target)
        return target

    with tempfile.TemporaryDirectory(prefix=".node-download-", dir=root) as temporary:
        staging = Path(temporary)
        archive = staging / f"{NODE_DIST}.tar.xz"
        url = f"https://nodejs.org/dist/v{NODE_VERSION}/{archive.name}"
        print(f"Downloading Node.js {NODE_VERSION}", file=sys.stderr)
        with urlopen(url, timeout=45) as response, archive.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if digest(archive) != NODE_SHA256:
            raise ValueError("Node release archive SHA-256 mismatch")
        with tarfile.open(archive) as source:
            source.extractall(staging, filter="data")
        extracted = staging / NODE_DIST
        _check_toolchain(extracted)
        node, npm = map(Path, _npm_argv(extracted))
        atomic_json(extracted / ".harnessgrad-node.json",
                    {"archive_sha256": NODE_SHA256, "node_sha256": digest(node), "npm_sha256": digest(npm)})
        extracted.rename(target)
    return target


def _locked_integrity(lockfile: Path, version: str, release: openclaw.Release | None) -> str:
    package = json.loads(lockfile.read_text())["packages"]["node_modules/openclaw"]
    integrity = package.get("integrity")
    if release is None:
        url = f"https://registry.npmjs.org/openclaw/-/openclaw-{version}.tgz"
        matches = (package.get("resolved") == url and isinstance(integrity, str)
                   and re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity))
    else:
        matches = integrity == release.integrity
    if package.get("version") != version or not matches:
        raise ValueError(f"npm lock does not match the published openclaw@{version} package")
    return integrity


def _install_package(instance: Path, node_root: Path, version: str, release: openclaw.Release | None) -> Path:
    project = instance / "package"
    project.mkdir()
    manifest = {"private": True, "dependencies": {"openclaw": version}}
    rebuild = release is None or release.rebuild
    if rebuild:
        manifest["allowScripts"] = {f"openclaw@{version}": True}
    (project / "package.json").write_text(
        json.dumps(manifest) + "\n",
        encoding="utf-8",
    )
    lockfile = project / "package-lock.json"
    print(f"Installing openclaw@{version}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix=".npm-", dir=instance) as temporary:
        scratch = Path(temporary)
        work = scratch / "empty"
        work.mkdir()
        env = {"PATH": os.pathsep.join((str(node_root / "bin"), os.defpath)),
               "NODE_DISABLE_COMPILE_CACHE": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
        for scope in ("user", "global"):
            config = scratch / f"{scope}.npmrc"
            config.touch()
            env[f"npm_config_{scope}config"] = str(config)
        for name, folder in {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
                             "XDG_CACHE_HOME": "cache", "TMPDIR": "tmp", "npm_config_cache": "npm-cache"}.items():
            path = scratch / folder
            path.mkdir()
            env[name] = str(path)
        npm = [*_npm_argv(node_root), "--registry=https://registry.npmjs.org",
               "--no-audit", "--no-fund", "--no-update-notifier"]

        def run_npm(*arguments: str) -> None:
            argv = sandbox.command([*npm, *arguments], platform=openclaw.ROOT,
                                   work_root=work, repo=work, task_dir=project, env=env,
                                   runtime_paths=(node_root,), writable_paths=(scratch,),
                                   platform_interpreter=False)
            result = subprocess.run(argv, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                    stdout=sys.stderr, timeout=900)
            if result.returncode:
                raise ValueError(f"npm {arguments[0]} failed with exit code {result.returncode}")

        run_npm("install", "--ignore-scripts", "--package-lock-only")
        _locked_integrity(lockfile, version, release)
        run_npm("ci", "--ignore-scripts")
        if rebuild:
            run_npm("rebuild", str(project / "node_modules/openclaw"))
    return lockfile


def _existing(receipt_path: Path, version: str, node_root: Path) -> Path:
    receipt = json.loads(receipt_path.read_text())
    name = receipt.get("instance")
    if (receipt.get("schema") != 1 or receipt.get("version") != version
            or receipt.get("node_version") != NODE_VERSION
            or receipt.get("npm_version") != NPM_VERSION or not isinstance(name, str)
            or not name.startswith("install-") or Path(name).name != name):
        raise ValueError(f"invalid installation receipt: {receipt_path}")
    instance = receipt_path.parent / name
    harness = instance / "harness"
    manifest = harness / "harness.json"
    lockfile = instance / "package" / "package-lock.json"
    release = openclaw.RELEASES.get(version)
    if (instance.is_symlink() or harness.is_symlink()
            or receipt.get("manifest_sha256") != digest(manifest)
            or receipt.get("lock_sha256") != digest(lockfile)
            or receipt.get("package_integrity") != _locked_integrity(lockfile, version, release)):
        raise ValueError(f"installed OpenClaw binding or lock changed: {instance}")
    resolved = openclaw._helper(harness).resolve(harness, {})
    if (resolved["node"] != str(node_root / "bin/node")
            or resolved["version"] != version
            or resolved["entrypoint"] != str(lockfile.parent / "node_modules/openclaw/openclaw.mjs")):
        raise ValueError(f"installed OpenClaw binding differs from the installation receipt: {harness}")
    if release is not None and resolved["source_identity"] != openclaw.source_identity(version):
        raise ValueError(f"installed OpenClaw source identity differs from the verified release: {harness}")
    openclaw.warn_unverified(resolved["source_identity"])
    return harness


def install_openclaw(version: str, root: Path) -> Path:
    source = openclaw.source_identity(version)
    recipe = openclaw.RELEASES.get(version)
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("OpenClaw installation currently supports Linux x86-64")
    root = root.expanduser().resolve()
    forbidden = (openclaw.ROOT, main_checkout(openclaw.ROOT))
    if root.is_relative_to("/tmp") or any(root.is_relative_to(path) or path.is_relative_to(root)
                                         for path in forbidden):
        raise ValueError("choose an installation root outside /tmp and the platform checkout")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    openclaw._helper(TEMPLATE).validate_binding(root, forbidden=forbidden)
    with locked(root, timeout_s=1800):
        release = root / "openclaw" / version
        if release.resolve() != release:
            raise ValueError(f"OpenClaw release must be stored inside the installation root: {release}")
        release.mkdir(parents=True, exist_ok=True)
        receipt_path = release / "installation.json"
        if receipt_path.is_symlink():
            raise ValueError(f"OpenClaw installation receipt must be an owned file: {receipt_path}")
        node_root = _toolchain(root)
        if receipt_path.exists():
            return _existing(receipt_path, version, node_root)
        openclaw.warn_unverified(source)
        instance = Path(tempfile.mkdtemp(prefix="install-", dir=release))
        try:
            lockfile = _install_package(instance, node_root, version, recipe)
            launcher = lockfile.parent / "node_modules/openclaw/openclaw.mjs"
            harness = instance / "harness"
            shutil.copytree(TEMPLATE, harness, ignore=shutil.ignore_patterns(
                ".git", ".env", ".state", "__pycache__", "*.pyc"))
            (harness / "runtime-identity.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
            openclaw.configure(harness, str(launcher), node=str(node_root / "bin/node"),
                               runtime_paths=(lockfile.parent,), env={"PATH": os.defpath}, warn=False)
            atomic_json(receipt_path, {"schema": 1, "version": version, "node_version": NODE_VERSION,
                        "npm_version": NPM_VERSION, "instance": instance.name,
                        "package_integrity": _locked_integrity(lockfile, version, recipe),
                        "lock_sha256": digest(lockfile), "manifest_sha256": digest(harness / "harness.json")})
        finally:
            if not receipt_path.exists():
                shutil.rmtree(instance)
        return harness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harnessgrad harness", description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="list verified harness versions")
    install = actions.add_parser("install", help="install and bind a harness version")
    install.add_argument("harness", choices=("openclaw",))
    install.add_argument("--version", help="exact release version (default: latest verified version)")
    install.add_argument("--install-root", type=Path, default=default_root())
    args = parser.parse_args(argv)
    if args.action == "list":
        for version in openclaw.RELEASES:
            print(f"openclaw {version} (Node.js {NODE_VERSION}, npm {NPM_VERSION}, Linux x86-64)")
        return 0
    try:
        version = args.version
        if version is None:
            version = openclaw.latest_verified_version()
            print(f"Using latest verified OpenClaw version {version} (--version omitted).", file=sys.stderr)
        harness = install_openclaw(version, args.install_root)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1
    print(harness)
    return 0
