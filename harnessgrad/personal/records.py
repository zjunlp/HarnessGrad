"""Immutable, content-addressed personal stage records outside the checkout."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tempfile

from harnessgrad.personal.contracts import MAX_BYTES, decode, digest, encode


def _directory(root):
    if any(path.is_symlink() or path.is_junction() for path in (root, *root.parents)):
        raise ValueError('stage record directory must be ordinary')


def _native_path(path):
    if os.name != 'nt':
        return Path(path)
    value = str(Path(path).absolute())
    if value.startswith('\\\\?\\'):
        return Path(value)
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


def _filename(record_id: str) -> str:
    if not isinstance(record_id, str) or re.fullmatch(r'[0-9a-f]{64}', record_id) is None:
        raise ValueError('invalid stage record ID')
    return record_id + '.json'


def read(root: Path, record_id: str) -> dict:
    root = _native_path(root)
    _directory(root)
    path = root / _filename(record_id)
    if path.is_symlink() or not path.is_file():
        raise ValueError('stage record must be an ordinary file')
    before = path.lstat()
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError('stage record must be single-link')
        value = decode(stream.read(MAX_BYTES + 1))
        after = os.fstat(stream.fileno())
    current = path.lstat()
    if any((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) !=
           (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
           for item in (after, current)):
        raise ValueError('stage record changed while reading')
    if digest(value) != record_id:
        raise ValueError('stage record digest mismatch')
    return value


def write(root: Path, value: dict) -> str:
    value = decode(encode(value))
    raw = (encode(value) + '\n').encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ValueError('stage record exceeds 1 MiB')
    root = _native_path(root)
    _directory(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    record_id = digest(value)
    destination = root / (record_id + '.json')
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if read(root, record_id) != value:
                raise ValueError('stage record conflicts with existing content')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return record_id


def recover(root: Path, *, record_id: str | None = None) -> tuple[str, dict] | None:
    """Recover a known publication, or the sole publication in a legacy directory."""
    root = _native_path(root)
    _directory(root)
    if record_id is not None:
        found = root / _filename(record_id)
    else:
        if not root.exists():
            return None
        found = None
        for path in root.iterdir():
            if path.name.startswith('.pending-'):
                continue
            if found is not None or path.suffix != '.json':
                raise ValueError('ambiguous stage record publication')
            found = path
        if found is None:
            return None
    try:
        published = found.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(published.st_mode):
        raise ValueError('stage record must be an ordinary file')
    # A process can exit between publishing the hardlink and removing its temp name.
    for temporary in root.glob('.pending-*'):
        try:
            pending = temporary.lstat()
        except FileNotFoundError:
            continue  # The original publisher may have finished its cleanup.
        if (stat.S_ISREG(pending.st_mode)
                and (pending.st_dev, pending.st_ino) == (published.st_dev, published.st_ino)):
            temporary.unlink(missing_ok=True)
    return found.stem, read(root, found.stem)
