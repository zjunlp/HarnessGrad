"""Cooperative .sources.json updates shared with round_publish.Publisher.

All writers must hold the persistent .sources.lock flock across read/modify/write;
never unlink the lock. The candidates root and its ancestors must be trusted and
stable, and same-UID or non-cooperating writers are outside this protocol. A
pending publisher transaction blocks all ordinary updates until reconciled.
Linux only; errors propagate, including ambiguous post-replace fsync failures.
"""
import fcntl
import json
import os
import secrets
import stat
import sys
from contextlib import contextmanager

PENDING = '__round_publish_pending__'
MAX_LEDGER = 16 * 1024 * 1024


class LedgerError(ValueError):
    """The ledger is unsafe, malformed, or awaiting reconciliation."""


def _regular(fd):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.geteuid():
        raise LedgerError('unsafe ledger or lock file')
    return st


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise LedgerError('duplicate ledger key')
        out[key] = value
    return out


class Ledger:
    def __init__(self, root_fd):
        self.root = root_fd

    def read(self):
        try:
            fd = os.open('.sources.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.root)
        except FileNotFoundError:
            return {}
        try:
            if _regular(fd).st_size > MAX_LEDGER:
                raise LedgerError('ledger too large')
            data = bytearray()
            while len(data) <= MAX_LEDGER:
                chunk = os.read(fd, min(65536, MAX_LEDGER + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_LEDGER:
                raise LedgerError('ledger too large')
            try:
                value = json.loads(data, object_pairs_hook=_unique,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite number')))
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise LedgerError('invalid ledger') from exc
            if not isinstance(value, dict) or PENDING in value:
                raise LedgerError('invalid or unresolved publication ledger')
            return value
        finally:
            os.close(fd)

    def write(self, value):
        if not isinstance(value, dict) or PENDING in value:
            raise LedgerError('invalid or unresolved publication ledger')
        data = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(',', ':')).encode('ascii') + b'\n'
        if len(data) > MAX_LEDGER:
            raise LedgerError('ledger too large')
        name = '.sources-tmp-' + secrets.token_hex(16)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.root)
        try:
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError('short ledger write')
                view = view[count:]
            os.fsync(fd)
            os.replace(name, '.sources.json', src_dir_fd=self.root, dst_dir_fd=self.root)
            os.fsync(self.root)
        finally:
            os.close(fd)
            try:
                os.unlink(name, dir_fd=self.root)
            except FileNotFoundError:
                pass


@contextmanager
def locked(root):
    """Yield a ledger while holding its cross-process exclusive lock."""
    if (sys.platform != 'linux' or not all(hasattr(os, x) for x in
            ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')) or os.open not in os.supports_dir_fd):
        raise LedgerError('secure ledger unsupported on this platform')
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        lock = os.open('.sources.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       0o600, dir_fd=root_fd)
        try:
            _regular(lock)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield Ledger(root_fd)
        finally:
            os.close(lock)
    finally:
        os.close(root_fd)
