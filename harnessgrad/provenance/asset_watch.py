"""Local Linux change detection for explicitly trusted verification assets."""
import ctypes
import os
from pathlib import Path
import re
import sys
import threading


_MOUNTS = Path('/proc/self/mountinfo')
_LOCAL_FILESYSTEMS = {b'ext2', b'ext3', b'ext4', b'xfs', b'btrfs', b'tmpfs'}

_MODIFY = 0x00000002
_ATTRIB = 0x00000004
_CLOSE_WRITE = 0x00000008
_MOVE = 0x000000c0
_CREATE = 0x00000100
_DELETE = 0x00000200
_DELETE_SELF = 0x00000400
_MOVE_SELF = 0x00000800
_DONT_FOLLOW = 0x02000000
_MASK = (_MODIFY | _ATTRIB | _CLOSE_WRITE | _MOVE | _CREATE | _DELETE
         | _DELETE_SELF | _MOVE_SELF | _DONT_FOLLOW)


class WatchUnavailable(Exception):
    """The source needs ordinary verification instead of a watch."""


def _mount_points(mounts):
    for line in mounts.splitlines():
        description, filesystem = line.split(b' - ', 1)
        target = re.sub(rb'\\([0-7]{3})', lambda match: bytes([int(match[1], 8)]),
                        description.split()[4])
        yield Path(os.fsdecode(target)), filesystem.split()[0]


class _Watch:
    def __init__(self, root):
        if sys.platform != 'linux':
            raise WatchUnavailable
        try:
            self.mounts = _MOUNTS.read_bytes()
            filesystems = []
            for target, filesystem in _mount_points(self.mounts):
                if target != root and target.is_relative_to(root):
                    raise WatchUnavailable
                if root.is_relative_to(target):
                    filesystems.append((len(target.parts), filesystem))
            if not filesystems or max(filesystems)[1] not in _LOCAL_FILESYSTEMS:
                raise WatchUnavailable
            libc = ctypes.CDLL(None, use_errno=True)
            initialize = libc.inotify_init1
            initialize.argtypes = (ctypes.c_int,)
            self.add_watch = libc.inotify_add_watch
            self.add_watch.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
            self.fd = initialize(os.O_NONBLOCK | os.O_CLOEXEC)
            if self.fd < 0:
                raise WatchUnavailable
        except (OSError, AttributeError, ValueError, IndexError) as exc:
            raise WatchUnavailable from exc

    def add(self, path):
        if self.add_watch(self.fd, os.fsencode(path), _MASK) < 0:
            raise WatchUnavailable

    def unchanged(self):
        try:
            if _MOUNTS.read_bytes() != self.mounts:
                return False
            # Any event, including overflow or a removed watch, loses the snapshot.
            os.read(self.fd, 4096)
        except BlockingIOError:
            return True
        except OSError:
            pass
        return False

    def close(self):
        os.close(self.fd)


class DirectoryChecks:
    """Reuse a tree digest for one evaluation while its filesystem watches hold."""
    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}
        self._unsupported = set()

    def digest(self, root, stamp, build):
        with self._lock:
            if root in self._entries:
                old_stamp, digest, watch = self._entries[root]
                if old_stamp == stamp and watch.unchanged():
                    return digest
                del self._entries[root]
                watch.close()
            if root not in self._unsupported:
                try:
                    watch = _Watch(root)
                    try:
                        digest = build(watch.add)
                        if watch.unchanged():
                            self._entries[root] = (stamp, digest, watch)
                            return digest
                    finally:
                        if root not in self._entries:
                            watch.close()
                except WatchUnavailable:
                    self._unsupported.add(root)
        return build()

    def close(self):
        for _, _, watch in self._entries.values():
            watch.close()
        self._entries.clear()
