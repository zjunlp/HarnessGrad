"""Shared byte inventories and atomic publication for external Python artifacts.

These helpers do not choose an interpreter, inspect imports or install packages.
The caller owns its trust boundary and runtime manifest schema.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def inventory(venv):
    result = {}
    interpreter = (venv / "bin/python").resolve(strict=True)
    for path in sorted(venv.rglob("*")):
        relative = str(path.relative_to(venv))
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(venv) and not (
                    relative.startswith("bin/python") and resolved == interpreter):
                raise ValueError(f"runtime symlink escapes the venv: {relative}")
            result[relative] = {"kind": "link", "target": os.readlink(path)}
        elif path.is_file():
            result[relative] = {"kind": "file", "sha256": digest(path),
                                "mode": stat.S_IMODE(path.stat().st_mode)}
        elif not path.is_dir():
            raise ValueError(f"runtime contains a non-regular entry: {relative}")
    return result


def freeze(venv):
    for path in sorted(venv.rglob("*"), reverse=True):
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    venv.chmod(stat.S_IMODE(venv.stat().st_mode) & ~0o222)


def remove_instance(instance):
    for parent, dirs, _ in os.walk(instance):
        Path(parent).chmod(0o700)
        for name in dirs:
            path = Path(parent) / name
            if not path.is_symlink():
                path.chmod(0o700)
    shutil.rmtree(instance)


@contextmanager
def locked(folder, *, timeout_s=None):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".prepare.lock").open("a") as lock:
        if timeout_s is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Timed out waiting for a runtime artifact lock")
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
        yield


def atomic_json(path, value):
    handle = tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".publish-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o444)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
