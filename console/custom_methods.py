"""Create-only local RSI method sources, with bounded and no-follow reads."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import secrets
import stat
from pathlib import Path

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z')
MAX_SOURCE = 256 * 1024


def read(root, name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('invalid method name')
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ValueError('secure method storage requires Linux')
    root_fd = fd = None
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(name + '.py', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE:
            raise ValueError('invalid method source')
        chunks = []
        remaining = MAX_SOURCE + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        after = os.fstat(fd)
        if len(data) > MAX_SOURCE or len(data) != info.st_size or (info.st_mtime_ns, info.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError('method source changed or exceeds size limit')
        return data, hashlib.sha256(data).hexdigest()
    finally:
        if fd is not None:
            os.close(fd)
        if root_fd is not None:
            os.close(root_fd)


def create(root, name, source):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('method name must contain 1–48 ASCII letters, digits, _ or -')
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ValueError('secure method storage requires Linux')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('method source is required')
    data = source.encode('utf-8')
    if len(data) > MAX_SOURCE:
        raise ValueError('method source exceeds size limit')
    try:
        ast.parse(source, filename='run.py')
    except (SyntaxError, ValueError) as error:
        raise ValueError(f'Python syntax error: {error}') from error
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    temporary = '.method-' + secrets.token_hex(16)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        with os.fdopen(fd, 'wb') as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, name + '.py', src_dir_fd=root_fd, dst_dir_fd=root_fd, follow_symlinks=False)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        os.close(root_fd)
    return hashlib.sha256(data).hexdigest()


def list_methods(root):
    rows = []
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        return rows
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return rows
    try:
        with os.scandir(fd) as entries:
            for entry in entries:
                if entry.name.endswith('.py') and NAME.fullmatch(entry.name[:-3]):
                    try:
                        if entry.is_file(follow_symlinks=False):
                            rows.append({'name': entry.name[:-3], 'title': entry.name[:-3], 'kind': 'custom'})
                    except OSError:
                        continue
    finally:
        os.close(fd)
    return sorted(rows, key=lambda row: row['name'])
