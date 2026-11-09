"""Bounded reads of console-owned launch logs.

Logs are diagnostic records and can contain task output.  This reader never
accepts a path from the caller and does not follow links or read special files.
"""
from __future__ import annotations

import os
import json
import re
import stat
import time

if __package__:
    from .assistant_drivers import redact_secrets
else:
    from assistant_drivers import redact_secrets

_RUN_ID = re.compile(r"console-[A-Za-z0-9._-]{1,180}\Z")
MAX_BYTES = 32_768


def exit_code(run_id, directory):
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return None
    root = fd = None
    try:
        root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(run_id + '.exit.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return None
        record = json.loads(os.read(fd, 4097))
        code = record.get('exit_code')
        if record.get('schema') == 1 and record.get('run_id') == run_id and type(code) is int:
            return code
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    finally:
        if fd is not None:
            os.close(fd)
        if root is not None:
            os.close(root)
    return None


def with_exit(row, directory):
    code = exit_code(row.get('id'), directory)
    if code is None:
        # A stopped process and a curve do not prove successful completion.
        # Older Console starts and interrupted workers have no exit receipt.
        if str(row.get('id') or '').startswith('console-') and row.get('status') == 'done':
            return dict(row, status='unknown')
        return row
    result = dict(row, launch_exit_code=code)
    if code != 0:
        result['status'] = 'failed'
    return result


def orphan_rows(directory, existing, live):
    """List console starts that have a log but no run directory yet.

    A log does not prove success or failure; stopped starts remain unknown.
    """
    rows = []
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        return rows
    root = None
    try:
        root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.name.endswith('.log'):
                    continue
                run_id = entry.name[:-4]
                if not _RUN_ID.fullmatch(run_id) or run_id in existing:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                rows.append({'id': run_id, 'pretty': run_id, 'stamp': '', 'tail': run_id,
                             'kind': 'unknown', 'status': 'running' if run_id in live else 'empty',
                             'rounds': 0, 'has_curve': False, 'curve_state': 'missing',
                             'curve_errors': 0, 'mtime_s': info.st_mtime,
                             'mtime': time.strftime('%m-%d %H:%M', time.localtime(info.st_mtime)),
                             'live': live.get(run_id), 'orphan_launch': True})
    except OSError:
        pass
    finally:
        if root is not None:
            os.close(root)
    return rows


def read(run_id, *, directory, environment, settings):
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return None
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        return None
    root = fd = None
    try:
        root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(run_id + '.log', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        os.lseek(fd, max(0, info.st_size - MAX_BYTES), os.SEEK_SET)
        raw = os.read(fd, MAX_BYTES).decode('utf-8', errors='replace')
        keys = [entry.get('api_key', '') for entry in settings.values() if isinstance(entry, dict)]
        return {'text': redact_secrets(raw, environment, secrets=keys, scrub_urls=True),
                'truncated': info.st_size > MAX_BYTES}
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)
        if root is not None:
            os.close(root)
