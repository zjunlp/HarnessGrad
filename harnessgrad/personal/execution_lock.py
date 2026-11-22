"""Nonblocking file lock for durable personal jobs across threads and processes."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat


class ExecutionLockUnavailable(ValueError):
    """The job's execution lock could not be acquired."""


@contextmanager
def execution_lock(path: Path, *, kind: str):
    """Close the handle to release the lock, including on process exit."""
    if path.is_symlink():
        raise ValueError(f'{kind} execution lock must be an ordinary file')
    with path.open('a+b') as stream:
        file = os.fstat(stream.fileno())
        if not stat.S_ISREG(file.st_mode):
            raise ValueError(f'{kind} execution lock must be an ordinary file')
        if file.st_nlink != 1:
            raise ValueError(f'{kind} execution lock must be single-link')
        if file.st_size == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ExecutionLockUnavailable(f'{kind} job already has an executor or its lock is unavailable') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
