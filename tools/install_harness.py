"""Install base harness releases into an experiment runtime store."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
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
from tools import loop_releases as loop
from tools import configure_codex_harness as codex
from tools import configure_dsh_harness as dsh
from tools import configure_opencode_harness as opencode
from tools import configure_openclaw_harness as openclaw

ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION = "24.22.2"
NPM_VERSION = "11.19.1"
NODE_DIST = f"node-v{NODE_VERSION}-linux-x64"
NODE_SHA256 = "48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75"
CODEX_PACKAGE = "@openai/codex"
CODEX_NATIVE_PACKAGE = "@openai/codex-linux-x64"
CODEX_RELEASES = {
    "0.166.0": (
        "sha512-2Szlp1l1cz5TX1ccpX4Zok3DZrot9xeZe0km+6/1v8JmqxADwyJxKBJah4dBymkwtS25j/u6dipJusTOI97Z8Q==",
        "sha512-CxDm3+D1cpbGJeHrdzINoG+3wn9BVncP4qpZaaNYkxe8iPtRJOYyCJnnPoVjWRIBvI9vPKbRoifsMSA5xDaUpw==",
    ),
    "0.167.0": (
        "sha512-1FSQgbTIKAdTub5Ln6z36NT4eXDmBRkinfigzycbgn9yLZdJ3UdkQZqr0N7jnl8/D1xxT/Pz3Fl2l7S2dmRaFA==",
        "sha512-FNc7danfUng9Sp6w+Fh7kofNHHZpA6VMyNKWsWrS3OwHly69gywTWeiuwrUPXpJSho8Df4xqRsHfHRPzj7zYQg==",
    ),
    "0.167.1": (
        "sha512-39uApTRT9bkhE8tMXTIfZxaZ27oi7hPqWoF2Coyp+iQFAD3NVHgH838IYVUfJaX85leMSP2s4JOmVw0mMSCN5Q==",
        "sha512-qZteglyvMCj1QVymMyPyKdtw38HaXCv5gvw8WN+N0+JJstu3k/d8xiYj9ciL7OwFLhcujgP8bCGJgaREeqrHkw==",
    ),
    "0.167.2": (
        "sha512-6OFkr2ndzTdFb32OEiUlM9BgpeY8+wnMe8vRhuvt+22TnxVR39KnJ22Ghay98IY9OmEltoNgFN4GALUkxFQCOw==",
        "sha512-2Rw3VkWonMxwCSgOw+NkiKLOuOzyoQNWmxU7x8GniesdMfhKc4asNqOzTgQwJLKCrSlPc4/6fYTaH7fYcuGWGQ==",
    ),
    "0.169.0": (
        "sha512-KhzYlkKPxoNssR8CEm45IjqnFWn7UtlF4CiD3tOJPea51xBs7MaHy0UQG9GSbCqk6Lrjn5QdJDV0QD1/FgWlGw==",
        "sha512-o05NyAOCDygSPRo1yinzxvkRSdmsZyBD9E7mbpmjRanSfM8lypqWtQgEo4XfNkwhuCR2aFu8mlGOsQGFXVbVww==",
    ),
}
OPENCODE_PACKAGES = {"1": "opencode-linux-x64", "2": "@opencode/cli-linux-x64"}
OPENCODE_RELEASES = {
    "1.19.5": "sha512-i1qh7VooLGML9xX6ud93y0JA8j/1aPeDYRKjsmYXrJX1TjIWHgBBMkEbEJPZthpWbsJg//XfBgR6Kb7+NPaJiQ==",
    "2.2.6": "sha512-g1NShGt+20y/7nzQvqAnO7bQshC8nZGaz8WC7J8vjgXIOZ1uzoxz5OiLnCbUWertf2CM3Dlh9tzrmZJf0ic9Aw==",
    "2.2.7": "sha512-wk/DPHxr4maiW+FmQp8ztoXlo/vC5qsmEIHsLNaNLZL2iaIYfhyqFRe6CRsCVxy+ib0sfK07X2IepFw7ZSKqNQ==",
}
DSH_RELEASES = {
    "0.2.2-rc.1": "sha512-ijR9UEVYkG15Q/Q79JzvzLBzQOQfc63VLr9n6flpwHPhr5IKn2UxthnSoto/P9l7qGd7pPvI1Ra6TW7LH3oKpg==",
}
EXACT_SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
LABELS = {"loop": "Loop", "openclaw": "OpenClaw", "codex": "Codex", "opencode": "OpenCode",
          "deepseek-harness": "DeepSeek Harness"}


@dataclass(frozen=True)
class NpmRelease:
    package: str
    version: str
    integrity: str | None
    rebuild: bool = False
    native_integrity: str | None = None


def _recipe(name: str, version: str) -> NpmRelease:
    if name == "codex":
        if not re.fullmatch(EXACT_SEMVER, version):
            raise ValueError("Codex version must be an exact X.Y.Z release")
        wrapper, native = CODEX_RELEASES.get(version, (None, None))
        return NpmRelease(CODEX_PACKAGE, version, wrapper, native_integrity=native)
    if name == "opencode":
        if not re.fullmatch(r"[12]\.[0-9]+\.[0-9]+", version):
            raise ValueError("OpenCode version must be an exact v1 or v2 release")
        package = OPENCODE_PACKAGES[version.split(".", 1)[0]]
        return NpmRelease(package, version, OPENCODE_RELEASES.get(version))
    if name == "deepseek-harness":
        if not re.fullmatch(EXACT_SEMVER + r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", version):
            raise ValueError("DeepSeek Harness version must be an exact X.Y.Z release or prerelease")
        return NpmRelease("@deepseek-ai/dsh", version, DSH_RELEASES.get(version))
    release = openclaw.RELEASES.get(version)
    return NpmRelease("openclaw", version, release.integrity if release else None,
                      rebuild=release is None or release.rebuild)


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


def _check_locked_package(package: dict, name: str, version: str, expected: str | None) -> str:
    integrity = package.get("integrity")
    if expected is None:
        filename = name.rsplit("/", 1)[-1]
        url = f"https://registry.npmjs.org/{name}/-/{filename}-{version}.tgz"
        matches = (package.get("resolved") == url and isinstance(integrity, str)
                   and re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity))
    else:
        matches = integrity == expected
    if package.get("version") != version or not matches:
        raise ValueError(f"npm lock does not match the published {name}@{version} package")
    return integrity


def _locked_integrity(lockfile: Path, release: NpmRelease) -> str:
    packages = json.loads(lockfile.read_text())["packages"]
    integrity = _check_locked_package(packages.get(f"node_modules/{release.package}", {}),
                                      release.package, release.version, release.integrity)
    if release.package == CODEX_PACKAGE:
        _check_locked_package(packages.get(f"node_modules/{CODEX_NATIVE_PACKAGE}", {}),
                              CODEX_PACKAGE, f"{release.version}-linux-x64", release.native_integrity)
    return integrity


def _install_package(instance: Path, node_root: Path, release: NpmRelease) -> Path:
    project = instance / "package"
    project.mkdir()
    manifest = {"private": True, "dependencies": {release.package: release.version}}
    if release.rebuild:
        manifest["allowScripts"] = {f"{release.package}@{release.version}": True}
    (project / "package.json").write_text(
        json.dumps(manifest) + "\n",
        encoding="utf-8",
    )
    lockfile = project / "package-lock.json"
    print(f"Installing {release.package}@{release.version}", file=sys.stderr)
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
            argv = sandbox.command([*npm, *arguments], platform=ROOT,
                                   work_root=work, repo=work, task_dir=project, env=env,
                                   runtime_paths=(node_root,), writable_paths=(scratch,),
                                   platform_interpreter=False)
            result = subprocess.run(argv, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                    stdout=sys.stderr, timeout=900)
            if result.returncode:
                raise ValueError(f"npm {arguments[0]} failed with exit code {result.returncode}")

        run_npm("install", "--ignore-scripts", "--package-lock-only")
        _locked_integrity(lockfile, release)
        run_npm("ci", "--ignore-scripts")
        if release.rebuild:
            run_npm("rebuild", str(project / "node_modules" / release.package))
    return lockfile


def _existing(receipt_path: Path, release: NpmRelease) -> Path:
    receipt = json.loads(receipt_path.read_text())
    name = receipt.get("instance")
    if (receipt.get("schema") != 1 or receipt.get("version") != release.version
            or receipt.get("node_version") != NODE_VERSION
            or receipt.get("npm_version") != NPM_VERSION or not isinstance(name, str)
            or not name.startswith("install-") or Path(name).name != name):
        raise ValueError(f"invalid installation receipt: {receipt_path}")
    instance = receipt_path.parent / name
    harness = instance / "harness"
    manifest = harness / "harness.json"
    lockfile = instance / "package" / "package-lock.json"
    if (instance.is_symlink() or harness.is_symlink()
            or receipt.get("manifest_sha256") != digest(manifest)
            or receipt.get("lock_sha256") != digest(lockfile)
            or receipt.get("package_integrity") != _locked_integrity(lockfile, release)):
        raise ValueError(f"installed {release.package} binding or lock changed: {instance}")
    return harness


def _check_binding(record: dict, argv: list[str], expected_version: str) -> None:
    if record["version"] != expected_version:
        raise ValueError(f"CLI version mismatch: expected {expected_version}, found {record['version']}")
    if record["argv"] != argv:
        raise ValueError(f"CLI binding differs from the selected launch command: {argv}")


def _version_key(version: str) -> tuple:
    base, _, prerelease = version.partition("-")
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part)
                        for part in prerelease.split(".")) if prerelease else ()
    return tuple(map(int, base.split("."))), not prerelease, identifiers


def install_harness(name: str, version: str, root: Path) -> Path:
    if name not in LABELS:
        raise ValueError(f"unknown base harness {name!r}")
    if name == "loop":
        return loop.install(version, root)
    recipe = _recipe(name, version)
    if name == "codex":
        template = ROOT / "base_harness/codex"
        helper, source = codex._adapter, None
        entrypoint, expected_version = "bin/codex.js", f"codex-cli {version}"
    elif name == "opencode":
        template = ROOT / "base_harness/opencode"
        helper, source = opencode._helper, None
        entrypoint, expected_version = "bin/opencode", version
    elif name == "deepseek-harness":
        template = dsh.TEMPLATE
        helper, source = dsh._helper, None
        entrypoint, expected_version = "lib/bin.js", version
    else:
        template = openclaw.TEMPLATE
        helper, source = openclaw._helper, openclaw.source_identity(version)
        entrypoint, expected_version = "openclaw.mjs", version
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError(f"{name} installation currently supports Linux x86-64")
    root = root.expanduser().resolve()
    forbidden = (ROOT, main_checkout(ROOT))
    if root.is_relative_to("/tmp") or any(root.is_relative_to(path) or path.is_relative_to(root)
                                         for path in forbidden):
        raise ValueError("choose an installation root outside /tmp and the platform checkout")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    helper(template).validate_binding(root, forbidden=forbidden)
    with locked(root, timeout_s=1800):
        release = root / name / version
        if release.resolve() != release:
            raise ValueError(f"{name} release must be stored inside the installation root: {release}")
        release.mkdir(parents=True, exist_ok=True)
        receipt_path = release / "installation.json"
        if receipt_path.is_symlink():
            raise ValueError(f"{name} installation receipt must be an owned file: {receipt_path}")
        node_root = _toolchain(root)
        node = node_root / "bin/node"
        launcher_path = Path("package/node_modules") / recipe.package / entrypoint
        prefix = [] if name == "opencode" else [str(node)]
        if source is None and recipe.integrity is None:
            print(f"Warning: {LABELS[name]} {version} is unverified. Trying its adapter.", file=sys.stderr)
        archived_receipt = None
        if receipt_path.exists():
            harness = _existing(receipt_path, recipe)
            record = helper(harness).resolve(harness, {})
            _check_binding(record, [*prefix, str(harness.parent / launcher_path)], expected_version)
            if source is None:
                return harness
            installed_source = record["source_identity"]
            if recipe.integrity is None or installed_source == source:
                openclaw.warn_unverified(installed_source)
                return harness
            if installed_source.get("verified") is not False or installed_source.get("upstream_commit") is not None:
                raise ValueError(f"installed OpenClaw source identity differs from the verified release: {harness}")
            archived_receipt = harness.parent / receipt_path.name
            print(f"Preparing a new verified OpenClaw {version} installation.", file=sys.stderr)
        if source is not None:
            openclaw.warn_unverified(source)
        instance = Path(tempfile.mkdtemp(prefix="install-", dir=release))
        try:
            lockfile = _install_package(instance, node_root, recipe)
            launcher = instance / launcher_path
            harness = instance / "harness"
            shutil.copytree(template, harness, ignore=shutil.ignore_patterns(
                ".git", ".env", ".state", "__pycache__", "*.pyc"))
            env = {"PATH": os.defpath}
            if name == "opencode":
                record = opencode.configure(harness, launcher, expected_version=version)
            elif name == "deepseek-harness":
                record = dsh.configure(harness, lockfile.parent, str(node), npm_version=version)["identity"]
            elif source is None:
                record = codex.configure(harness, str(launcher), node=str(node), env=env,
                                         sandbox_only=recipe.integrity is None)
            else:
                (harness / "runtime-identity.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
                record = openclaw.configure(harness, str(launcher), node=str(node),
                                             runtime_paths=(lockfile.parent,), env=env, warn=False)
            _check_binding(record, [*prefix, str(launcher)], expected_version)
            receipt = {"schema": 1, "version": version, "node_version": NODE_VERSION,
                       "npm_version": NPM_VERSION, "instance": instance.name,
                       "package_integrity": _locked_integrity(lockfile, recipe),
                       "lock_sha256": digest(lockfile), "manifest_sha256": digest(harness / "harness.json")}
            if archived_receipt is not None:
                # Keep the original receipt when the active path is atomically replaced.
                try:
                    os.link(receipt_path, archived_receipt, follow_symlinks=False)
                except FileExistsError:
                    if archived_receipt.is_symlink() or not archived_receipt.samefile(receipt_path):
                        raise ValueError(f"cannot preserve original OpenClaw receipt: {archived_receipt}")
            atomic_json(receipt_path, receipt)
        except BaseException:
            if not receipt_path.exists() or json.loads(receipt_path.read_text()).get("instance") != instance.name:
                shutil.rmtree(instance)
            raise
        return harness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harnessgrad harness", description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="list verified harness versions")
    install = actions.add_parser("install", help="install and bind a harness version")
    catalogs = {"loop": loop.RELEASES, "openclaw": openclaw.RELEASES, "codex": CODEX_RELEASES, "opencode": OPENCODE_RELEASES,
                "deepseek-harness": DSH_RELEASES}
    install.add_argument("harness", choices=catalogs)
    install.add_argument("--version", help="exact release version (default: latest verified version)")
    install.add_argument("--install-root", type=Path, default=default_root())
    args = parser.parse_args(argv)
    if args.action == "list":
        for name, releases in catalogs.items():
            for version in releases:
                if name == "loop":
                    print(f"loop {version} (bundled Python source; prepare dependencies separately)")
                else:
                    print(f"{name} {version} (Node.js {NODE_VERSION}, npm {NPM_VERSION}, Linux x86-64)")
        return 0
    try:
        version = args.version
        if version is None:
            version = max(catalogs[args.harness], key=_version_key)
            name = LABELS[args.harness]
            print(f"Using latest verified {name} version {version} (--version omitted).", file=sys.stderr)
        harness = install_harness(args.harness, version, args.install_root)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1
    print(harness)
    return 0
