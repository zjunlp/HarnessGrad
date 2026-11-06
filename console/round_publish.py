"""Private round staging and collision-safe publication (Linux only).

The candidates root and its ancestors must be trusted and stable, and only
cooperating publishers may mutate .sources.json or .round-staging. Same-UID
adversaries and concurrent modification of a stage by its creator are outside
this API's protection. Ordinary uncommitted stages are removed on context exit;
crash-abandoned stages remain for inspection.

Use ``with Publisher(root) as p:``; ``s = p.stage()`` returns an owned Stage
whose directory fd can be handed to a materializer. After it finishes, call
``p.publish(s, base, provenance)``. It returns the actual candidate name. Close
stages with ``s.close()``. Names are base, base-2, base-3, etc. Provenance must
be a JSON object; it is stored verbatim under the selected name in .sources.json.

A durable pending entry is written before rename, then replaced by provenance
under the same lock. If a crash or an I/O failure interrupts publication, the
pending entry is retained. A later publication reconciles it only when the
recorded directory identity proves whether rename occurred; ambiguity fails closed. In particular an exception after rename does not imply that no
candidate was published. Atomicity is per ledger update, not a transaction
across the ledger and candidate directory; callers must not infer success from
an unverified pending entry. Existing legacy ledger objects are preserved.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import secrets
import shutil
import stat
import sys


class PublishError(RuntimeError):
    """Unsafe state or publication failure requiring caller attention."""


_DIR = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
_FILE = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)
_PENDING = '__round_publish_pending__'
_MAX_LEDGER = 16 * 1024 * 1024


def _component(value):
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or '/' in value or '\x00' in value or value.startswith('.')
            or len(os.fsencode(value)) > 240):
        raise ValueError('invalid candidate name')
    return value


def _private_dir(fd):
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise PublishError('staging directory must be owned by the current user and mode 0700')


def _regular(fd):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.geteuid():
        raise PublishError('unsafe ledger or lock file')
    return st


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublishError('duplicate ledger key')
        result[key] = value
    return result


def _rename_noreplace(srcfd, src, dstfd, dst):
    libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, 'renameat2', None)
    if fn is None:
        raise PublishError('renameat2 unavailable')
    fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    fn.restype = ctypes.c_int
    if fn(srcfd, os.fsencode(src), dstfd, os.fsencode(dst), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), dst)


class Stage:
    """A private stage directory; close the descriptor after use."""
    def __init__(self, publisher, name, fd):
        self._publisher, self.name, self.fd = publisher, name, fd
        self._published = False
        self._pending = False

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            if self.fd is not None and not self._published and not self._pending:
                original = os.fstat(self.fd)
                current = os.stat(self.name, dir_fd=self._publisher.staging, follow_symlinks=False)
                if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                    raise PublishError('stage directory changed during cleanup')
                shutil.rmtree(self.name, dir_fd=self._publisher.staging)
        finally:
            self.close()


class Publisher:
    def __init__(self, candidates_root):
        if (sys.platform != 'linux' or not all(hasattr(os, x) for x in
                ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')) or os.open not in os.supports_dir_fd):
            raise PublishError('secure publication unsupported on this platform')
        self.root = os.open(candidates_root, _DIR)
        self.staging = None
        try:
            try:
                os.mkdir('.round-staging', 0o700, dir_fd=self.root)
            except FileExistsError:
                pass
            self.staging = os.open('.round-staging', _DIR, dir_fd=self.root)
            _private_dir(self.staging)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.staging is not None:
            os.close(self.staging)
            self.staging = None
        if self.root is not None:
            os.close(self.root)
            self.root = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def stage(self):
        """Create a fresh private directory; caller owns the returned fd."""
        for _ in range(128):
            name = 'stage-' + secrets.token_hex(16)
            try:
                os.mkdir(name, 0o700, dir_fd=self.staging)
            except FileExistsError:
                continue
            fd = os.open(name, _DIR, dir_fd=self.staging)
            try:
                _private_dir(fd)
            except BaseException:
                os.close(fd)
                raise
            return Stage(self, name, fd)
        raise PublishError('cannot allocate private stage')

    def _ledger(self, *, allow_pending=False):
        try:
            fd = os.open('.sources.json', _FILE, dir_fd=self.root)
        except FileNotFoundError:
            return {}
        try:
            st = _regular(fd)
            if st.st_size > _MAX_LEDGER:
                raise PublishError('ledger too large')
            data = bytearray()
            while len(data) <= _MAX_LEDGER:
                chunk = os.read(fd, min(65536, _MAX_LEDGER + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > _MAX_LEDGER:
                raise PublishError('ledger too large')
            try:
                value = json.loads(data, object_pairs_hook=_unique)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise PublishError('invalid ledger') from exc
            if not isinstance(value, dict) or (_PENDING in value and not allow_pending):
                raise PublishError('invalid or unresolved publication ledger')
            return value
        finally:
            os.close(fd)

    def _write_ledger(self, ledger):
        data = json.dumps(ledger, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(',', ':')).encode('ascii') + b'\n'
        if len(data) > _MAX_LEDGER:
            raise PublishError('ledger too large')
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

    def _reconcile_locked(self):
        ledger = self._ledger(allow_pending=True)
        pending = ledger.get(_PENDING)
        if pending is None:
            return ledger
        if not isinstance(pending, dict) or set(pending) != {'candidate', 'stage', 'provenance', 'dev', 'ino'}:
            raise PublishError('ambiguous pending publication; manual reconciliation required')
        try:
            name = _component(pending['candidate'])
            stage_name = _component(pending['stage'])
            if not stage_name.startswith('stage-') or not isinstance(pending['provenance'], dict):
                raise ValueError()
            identity = (pending['dev'], pending['ino'])
            if not all(type(x) is int and x >= 0 for x in identity) or name in ledger:
                raise ValueError()
            def identity_at(parent, component):
                try:
                    st = os.stat(component, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return None
                return (st.st_dev, st.st_ino) if stat.S_ISDIR(st.st_mode) else ()
            source = identity_at(self.staging, stage_name)
            target = identity_at(self.root, name)
            if source == identity and target is None:
                # Rename had not happened. Preserve the private stage for inspection.
                del ledger[_PENDING]
            elif source is None and target == identity:
                del ledger[_PENDING]
                ledger[name] = pending['provenance']
            else:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise PublishError('ambiguous pending publication; manual reconciliation required') from None
        self._write_ledger(ledger)
        return ledger

    def publish(self, stage, base, provenance, *, finalize=None):
        """Publish stage exactly once, returning a collision-free candidate name.

        Any error after a pending ledger write may require reconciliation.
        The caller must stop writing to the stage before entering this method.
        """
        _component(base)
        if not isinstance(stage, Stage) or stage._publisher is not self or stage.fd is None or stage._published:
            raise ValueError('invalid or already published stage')
        if not isinstance(provenance, dict) or _PENDING in provenance:
            raise ValueError('provenance must be a JSON object')
        # Validate and detach caller-owned mutable data before changing the ledger.
        try:
            provenance = json.loads(json.dumps(provenance, allow_nan=False))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError('invalid provenance') from exc
        _private_dir(stage.fd)
        original = os.fstat(stage.fd)
        current = os.stat(stage.name, dir_fd=self.staging, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
            raise PublishError('stage directory changed')
        lock = os.open('.sources.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       0o600, dir_fd=self.root)
        try:
            _regular(lock)
            fcntl.flock(lock, fcntl.LOCK_EX)
            ledger = self._reconcile_locked()
            for index in range(1, 100001):
                name = base if index == 1 else f'{base}-{index}'
                if name in ledger:
                    continue
                # Finalize inside the lock for the actual selected name. A collision
                # with a non-cooperating creator can cause another invocation.
                current_provenance = finalize(name) if finalize is not None else provenance
                if not isinstance(current_provenance, dict) or _PENDING in current_provenance:
                    raise ValueError('invalid finalized provenance')
                current_provenance = json.loads(json.dumps(current_provenance, allow_nan=False))
                ledger[_PENDING] = {'candidate': name, 'stage': stage.name, 'provenance': current_provenance, 'dev': original.st_dev, 'ino': original.st_ino}
                stage._pending = True
                try:
                    self._write_ledger(ledger)
                except BaseException:
                    # A failed write can have replaced the ledger before failing.
                    # Under the same lock, only clear ownership when a fresh read
                    # proves no pending transaction references this stage.
                    try:
                        persisted = self._ledger(allow_pending=True)
                        pending = persisted.get(_PENDING)
                        if _PENDING not in persisted or (isinstance(pending, dict) and
                                isinstance(pending.get('stage'), str) and pending['stage'] != stage.name):
                            stage._pending = False
                    except (OSError, ValueError, PublishError):
                        pass
                    raise
                try:
                    _rename_noreplace(self.staging, stage.name, self.root, name)
                except OSError as exc:
                    if exc.errno != errno.EEXIST:
                        raise
                    del ledger[_PENDING]
                    self._write_ledger(ledger)
                    stage._pending = False
                    continue
                stage._published = True
                os.fsync(self.staging)
                os.fsync(self.root)
                del ledger[_PENDING]
                ledger[name] = current_provenance
                self._write_ledger(ledger)
                return name
            raise PublishError('candidate collision limit exceeded')
        finally:
            os.close(lock)
