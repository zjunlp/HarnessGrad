"""Bounded, descriptor-relative reads of untrusted round recovery artifacts.

These functions do not provide a point-in-time snapshot if files are modified
concurrently. Callers must publish results only after their own validation.
"""
from contextlib import contextmanager
import fnmatch
import os
from pathlib import Path
import shutil
import stat


class RecoveryArtifactError(ValueError):
    """A recovery artifact is unsafe or exceeds the configured limits."""


_DIR = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
_FILE = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)


def _require_secure_platform():
    import sys
    if (sys.platform != 'linux' or not all(hasattr(os, flag) for flag in
            ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')) or os.open not in os.supports_dir_fd):
        raise RecoveryArtifactError('secure recovery unsupported on this platform')


def _component(name):
    if not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\x00' in name:
        raise RecoveryArtifactError('invalid artifact path component')
    return name


def _open_regular(name, directory_fd):
    """Classify without opening a device, then reopen the pinned regular inode."""
    if not hasattr(os, 'O_PATH'):
        raise RecoveryArtifactError('secure regular file opening unavailable')
    path_fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
    try:
        if stat.S_ISLNK(os.fstat(path_fd).st_mode):
            raise OSError('artifact is a symbolic link')
        before = _regular(path_fd)
        fd = os.open(f'/proc/self/fd/{path_fd}', _FILE & ~os.O_NOFOLLOW)
        try:
            after = _regular(fd)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise RecoveryArtifactError('artifact changed while opening')
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(path_fd)


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RecoveryArtifactError('artifact must be a regular, singly linked file')
    return info


@contextmanager
def open_run_dir(run_path):
    """Pin a run directory once; ancestors of run_path must be trusted.

    The yielded descriptor is owned by this context and may be passed as run_fd
    to recovery helpers. Renaming or replacing the path does not retarget it.
    """
    _require_secure_platform()
    with _run_dir(run_path, None) as fd:
        yield fd


@contextmanager
def open_snapshot_fd(run_path, snapshot_name, *, max_bytes=32 * 1024 * 1024, run_fd=None):
    """Yield a pinned regular snapshot fd; the consumer must bound its reads.

    A size check here is only preliminary: another writer can grow or modify
    the inode after it is opened. The yielded descriptor is closed on exit.
    """
    _require_secure_platform()
    _component(snapshot_name)
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('invalid byte limit')
    with _run_dir(run_path, run_fd) as run:
        with _open_dir('snapshots', dir_fd=run) as snapshots:
            fd = _open_regular(snapshot_name, snapshots)
            try:
                if _regular(fd).st_size > max_bytes:
                    raise RecoveryArtifactError('snapshot byte limit exceeded')
                yield fd
            finally:
                os.close(fd)


def read_snapshot(run_path, snapshot_name, *, max_bytes=32 * 1024 * 1024, run_fd=None):
    """Read snapshots/<snapshot_name> without following links, bounded by max_bytes.

    Missing files and OS errors propagate; unsafe types and oversize data raise
    RecoveryArtifactError. The returned bytes can be passed to a snapshot parser.
    """
    _require_secure_platform()
    _component(snapshot_name)
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('invalid byte limit')
    with _run_dir(run_path, run_fd) as run:
        with _open_dir('snapshots', dir_fd=run) as snapshots:
            fd = _open_regular(snapshot_name, snapshots)
            try:
                info = _regular(fd)
                if info.st_size > max_bytes:
                    raise RecoveryArtifactError('snapshot byte limit exceeded')
                chunks, total = [], 0
                while True:
                    chunk = os.read(fd, min(65536, max_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise RecoveryArtifactError('snapshot byte limit exceeded')
                    chunks.append(chunk)
                return b''.join(chunks)
            finally:
                os.close(fd)


class _open_dir:
    def __init__(self, path, *, dir_fd=None):
        self.path, self.dir_fd = path, dir_fd

    def __enter__(self):
        self.fd = os.open(self.path, _DIR, dir_fd=self.dir_fd)
        return self.fd

    def __exit__(self, *args):
        os.close(self.fd)


class _run_dir:
    def __init__(self, path, fd):
        self.path, self.original = path, fd

    def __enter__(self):
        self.fd = os.dup(self.original) if self.original is not None else os.open(self.path, _DIR)
        if not stat.S_ISDIR(os.fstat(self.fd).st_mode):
            os.close(self.fd)
            raise RecoveryArtifactError('run is not a directory')
        return self.fd

    def __exit__(self, *args):
        os.close(self.fd)


def _names(directory_fd):
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            yield entry.name


def copy_state_into(run_path, state_name, dest_fd, *, max_files=10000,
                    max_bytes=128 * 1024 * 1024, max_depth=64, run_fd=None):
    """Copy into an empty, private pinned directory; leave partial output on error.

    Counts include directories and ignored names. The caller owns dest_fd and is
    responsible for cleanup. Ancestors of run_path must be trusted.
    """
    _require_secure_platform()
    _component(state_name)
    if any(type(n) is not int or n < 0 for n in (max_files, max_bytes, max_depth)):
        raise ValueError('invalid recovery limit')
    if not stat.S_ISDIR(os.fstat(dest_fd).st_mode):
        raise RecoveryArtifactError('destination is not a directory')
    if os.listdir(dest_fd):
        raise RecoveryArtifactError('destination must be empty')
    count = total = 0

    def copy_dir(src, dst, depth):
        nonlocal count, total
        if depth > max_depth:
            raise RecoveryArtifactError('state depth limit exceeded')
        for name in _names(src):
            _component(name)
            count += 1
            if count > max_files:
                raise RecoveryArtifactError('state entry limit exceeded')
            info = os.stat(name, dir_fd=src, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise RecoveryArtifactError('state contains a link')
            if name in ('__pycache__', '.git', '.state') or fnmatch.fnmatchcase(name, '*.pyc'):
                continue
            if stat.S_ISDIR(info.st_mode):
                with _open_dir(name, dir_fd=src) as child:
                    os.mkdir(name, 0o700, dir_fd=dst)
                    with _open_dir(name, dir_fd=dst) as out:
                        copy_dir(child, out, depth + 1)
            elif stat.S_ISREG(info.st_mode):
                fd = _open_regular(name, src)
                try:
                    actual = _regular(fd)
                    if actual.st_size > max_bytes - total:
                        raise RecoveryArtifactError('state byte limit exceeded')
                    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  0o600 | (actual.st_mode & 0o111), dir_fd=dst)
                    try:
                        while True:
                            chunk = os.read(fd, min(65536, max_bytes - total + 1))
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > max_bytes:
                                raise RecoveryArtifactError('state byte limit exceeded')
                            view = memoryview(chunk)
                            while view:
                                written = os.write(out, view)
                                if not written:
                                    raise OSError('short write copying state')
                                view = view[written:]
                    finally:
                        os.close(out)
                finally:
                    os.close(fd)
            else:
                raise RecoveryArtifactError('state contains a link or special file')

    with _run_dir(run_path, run_fd) as run:
        with _open_dir(state_name, dir_fd=run) as source:
            try:
                copy_dir(source, dest_fd, 0)
            except FileNotFoundError as exc:
                raise RecoveryArtifactError('state changed during copy') from exc


def copy_state(run_path, state_name, target, *, max_files=10000,
               max_bytes=128 * 1024 * 1024, max_depth=64, run_fd=None):
    """Copy into a new target under a trusted parent, cleaning up on failure."""
    _require_secure_platform()
    target = Path(target)
    target.mkdir(mode=0o700)
    try:
        with _open_dir(target) as destination:
            copy_state_into(run_path, state_name, destination, max_files=max_files,
                            max_bytes=max_bytes, max_depth=max_depth, run_fd=run_fd)
    except BaseException:
        shutil.rmtree(target)
        raise
