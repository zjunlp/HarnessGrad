"""Stdlib-only Bun workspace reuse, shared with sandboxed methods.

No installers, provider access or platform/scorer imports live here.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
from pathlib import Path

BUN_GENERATED = frozenset({".git", "node_modules", ".state", "__pycache__", ".opencode-state", "_harnessgrad"})

class RuntimeUnavailable(ValueError):
    pass


def source_files(source: Path):
    for directory, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in BUN_GENERATED)
        for name in sorted(files):
            yield Path(directory) / name


def dependency_identity(source: Path, bun_version: str, system: str, arch: str,
                        *, package: str = "", binary_sha256: str = "") -> str:
    source = source.resolve()
    digest = hashlib.sha256(json.dumps(
        [bun_version, system, arch, package, binary_sha256, "declared-bun-v2"], separators=(",", ":")).encode())
    inputs = set()
    for path in source_files(source):
        if path.name not in {"package.json", "bun.lock", "bun.lockb", "bunfig.toml", ".npmrc"}:
            continue
        inputs.add(path)
        if path.name != "package.json":
            continue
        manifest = json.loads(path.read_text())
        # A local package cannot become an immutable third-party store entry.
        # Workspace packages are linked into each candidate separately.
        def reject_local(value):
            if isinstance(value, dict):
                for item in value.values():
                    reject_local(item)
            elif isinstance(value, list):
                for item in value:
                    reject_local(item)
            elif isinstance(value, str) and value.startswith(("file:", "link:", "./", "../", "/", "~", "\\")):
                raise RuntimeUnavailable(
                    f"Unsupported local dependency in {path.relative_to(source)}; use a workspace package")
        for field in ("dependencies", "devDependencies", "optionalDependencies",
                      "peerDependencies", "overrides", "resolutions", "catalog", "catalogs"):
            reject_local(manifest.get(field))
        if isinstance(manifest.get("workspaces"), dict):
            for field in ("catalog", "catalogs"):
                reject_local(manifest["workspaces"].get(field))
        patches = manifest.get("patchedDependencies", {})
        if not isinstance(patches, dict):
            raise RuntimeUnavailable("Bun patchedDependencies must be an object")
        for relative in patches.values():
            if not isinstance(relative, str):
                raise RuntimeUnavailable("Bun patch paths must be strings")
            patch = (path.parent / relative).resolve()
            if not patch.is_relative_to(source) or not patch.is_file():
                raise RuntimeUnavailable("Bun patch input must exist inside candidate source")
            inputs.add(patch)
    for path in sorted(inputs):
        digest.update(path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def _module_dirs(source):
    for directory, dirs, _ in os.walk(source):
        if "node_modules" in dirs:
            yield Path(directory) / "node_modules"
        dirs[:] = [d for d in dirs if d not in BUN_GENERATED]


def attach_layout(source, store):
    """Serialize host preparation; the sandbox only calls attach_fresh on a fresh clone."""
    import fcntl
    lock_name = hashlib.sha256(str(source).encode()).hexdigest() + ".attach.lock"
    with (store.parent / lock_name).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        attach_fresh(source, store)


def attach_fresh(source, store):
    """Attach a verified read-only store without writing lock files alongside it."""
    layout = json.loads((store / "layout.json").read_text())
    if not isinstance(layout, list) or not layout or not (store / "packages").is_dir():
        raise RuntimeUnavailable("Bun dependency cache layout is invalid")
    for relative in layout:
        if (not isinstance(relative, str) or Path(relative).is_absolute()
                or ".." in Path(relative).parts or Path(relative).name != "node_modules"
                or not (store / "layout" / relative).is_dir()):
            raise RuntimeUnavailable("Bun dependency cache layout is invalid")
    shared = source / "node_modules/.bun"
    correct = shared.is_symlink() and shared.resolve() == store / "packages"
    correct &= {p.relative_to(source).as_posix() for p in _module_dirs(source)} == set(layout) | {"node_modules"}
    for relative in layout:
        expected, actual = store / "layout" / relative, source / relative
        if not actual.is_dir() or actual.is_symlink():
            correct = False
            continue
        for path in expected.rglob("*"):
            target = actual / path.relative_to(expected)
            if path.is_symlink():
                correct &= target.is_symlink() and os.readlink(path) == os.readlink(target)
            elif path.is_file():
                correct &= target.is_file() and not target.is_symlink() and path.read_bytes() == target.read_bytes()
            else:
                correct &= target.is_dir() and not target.is_symlink()
        expected_names = {p.relative_to(expected) for p in expected.rglob("*")}
        actual_names = {p.relative_to(actual) for p in actual.rglob("*")
                        if ".bun" not in p.relative_to(actual).parts}
        correct &= expected_names == actual_names
    if correct:
        return
    for directory in list(_module_dirs(source)):
        if directory.is_symlink():
            directory.unlink()
        else:
            shutil.rmtree(directory)
    for relative in layout:
        shutil.copytree(store / "layout" / relative, source / relative, symlinks=True)
    shared.parent.mkdir(exist_ok=True)
    shared.symlink_to(store / "packages", target_is_directory=True)
