"""Shared byte inventories and atomic publication for external Python artifacts.

These helpers do not choose an interpreter, inspect imports or install packages.
The caller owns its trust boundary and runtime manifest schema.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from functools import lru_cache
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


def _file_stat_key(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@lru_cache(maxsize=32768)
def _frozen_digest(path, stat_key):
    value = digest(path)
    if _file_stat_key(os.lstat(path)) != stat_key:
        raise ValueError(f"runtime file inventory changed during hashing: {path}")
    return value


def frozen_inventory_device(venv):
    """Enable stat-keyed reuse on supported local Linux filesystems."""
    device = venv.stat().st_dev
    mount_device = f"{os.major(device)}:{os.minor(device)}"
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as mounts:
            for line in mounts:
                fields, _, filesystem = line.partition(" - ")
                if fields.split(" ", 3)[2] == mount_device:
                    if filesystem.split(" ", 1)[0] in {"ext4", "xfs", "btrfs", "tmpfs", "overlay"}:
                        return device
                    return None
    except OSError:
        pass
    return None


def inventory_digest(path, info, *, frozen_device):
    """Reuse byte digests for frozen artifact files with the same inode generation."""
    # A fresh ctime can still share the filesystem's timestamp tick with a write.
    if (info.st_dev == frozen_device and info.st_ino and info.st_nlink == 1 and not info.st_mode & 0o222
            and 1_000_000_000 <= time.time_ns() - info.st_ctime_ns):
        return _frozen_digest(path, _file_stat_key(info))
    return digest(path)


def inventory_entries(venv):
    """Walk relative names with one fresh lstat per entry and no directory symlink traversal."""
    directories = [("", os.fspath(venv))]
    while directories:
        prefix, directory = directories.pop()
        with os.scandir(directory) as children:
            for entry in children:
                relative = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    directories.append((relative + "/", entry.path))
                else:
                    yield relative, entry.path, info


def inventory(venv, *, reuse_frozen=False):
    result = {}
    interpreter = (venv / "bin/python").resolve(strict=True)
    frozen_device = frozen_inventory_device(venv) if reuse_frozen else None
    for relative, path, info in inventory_entries(venv):
        if stat.S_ISLNK(info.st_mode):
            resolved = Path(path).resolve(strict=True)
            if not resolved.is_relative_to(venv) and not (
                    relative.startswith("bin/python") and resolved == interpreter):
                raise ValueError(f"runtime symlink escapes the venv: {relative}")
            result[relative] = {"kind": "link", "target": os.readlink(path)}
        elif stat.S_ISREG(info.st_mode):
            result[relative] = {"kind": "file", "sha256": inventory_digest(path, info, frozen_device=frozen_device),
                                "mode": stat.S_IMODE(info.st_mode)}
        else:
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
