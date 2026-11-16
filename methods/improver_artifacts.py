"""Persist only producer-declared workspace artifacts, never inferred native bodies.

Protocol: version=1, session_id, artifacts=[{path, sha256, tool_call_id?,
session_id?, producer?, coverage?}]. Paths are relative to allowed_root. Limits
below bound persistence bytes, not model execution or context budgets.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat

try:
    from .improver_archive import ArchiveFailure, _directory
except ImportError:  # sibling loading inside the isolated methods-only sandbox
    import _siblings
    _archive = _siblings.load('improver_archive')
    ArchiveFailure, _directory = _archive.ArchiveFailure, _archive._directory

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACTS = 1024


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('Invalid artifact relative path')
    return PurePosixPath(value).parts


def _read(root_fd, parts, limit):
    directory = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Artifact must be a regular file with one link')
            if info.st_size > limit:
                raise ValueError('Artifact persistence byte limit exceeded')
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Artifact persistence byte limit exceeded')
            return data
    finally:
        os.close(directory)


def persist_artifact_manifest(manifest_path, *, allowed_root, destination, credentials=()):
    """Copy declared bytes to private .log files; fail terminally with durable receipts.

    Source hashes describe original bytes; persisted hashes describe redacted bytes.
    Producer coverage claims are retained separately from verified byte coverage.
    Missing manifests mean no declaration; declared missing files are failures.
    """
    records = {'status': 'pending', 'artifacts': [],
               'native_body_coverage': 'not_declared',
               'persistence_limits': {'per_file_bytes': MAX_FILE_BYTES,
                                      'total_bytes': MAX_TOTAL_BYTES}}
    root_fd = destination_fd = None
    prefix = 'improver-artifacts-' + secrets.token_hex(16)
    secret_bytes = sorted({str(s).encode() for s in credentials if s}, key=len, reverse=True)

    def redact(data):
        for secret in secret_bytes:
            data = data.replace(secret, b'[REDACTED]')
        return data

    def write(name, data):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=destination_fd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return {'path': str(destination_path / name), 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}

    def receipt():
        data = redact(json.dumps(records, sort_keys=True).encode())
        records['manifest'] = write(prefix + '-manifest.log', data)
        os.fsync(destination_fd)

    try:
        root, root_fd = _directory(allowed_root)
        destination_path, destination_fd = _directory(destination)
        manifest = Path(os.path.abspath(manifest_path))
        parts = _relative(str(manifest.relative_to(root)))
        try:
            raw = _read(root_fd, parts, MAX_MANIFEST_BYTES)
        except FileNotFoundError:
            records['status'] = 'not_declared'
            receipt()
            return records
        # Reject credential-bearing metadata rather than leaking it into paths or receipts.
        if any(secret in raw for secret in secret_bytes):
            raise ValueError('Credential found in artifact declaration')
        declaration = json.loads(raw)
        if not isinstance(declaration, dict) or declaration.get('version') != 1:
            raise ValueError('Invalid artifact manifest version')
        session = declaration.get('session_id')
        entries = declaration.get('artifacts')
        if not isinstance(session, str) or not session.strip() or not isinstance(entries, list) or len(entries) > MAX_ARTIFACTS:
            raise ValueError('Invalid artifact manifest identity or entries')
        records.update(session_id=session, status='declared', native_body_coverage='producer_declared_only')
        seen = set()
        total = 0
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError('Invalid artifact entry')
            relative = entry.get('path')
            source_parts = _relative(relative)
            if relative in seen:
                raise ValueError('Duplicate artifact path')
            seen.add(relative)
            expected = entry.get('sha256')
            if (not isinstance(expected, str) or len(expected) != 64
                    or any(c not in '0123456789abcdef' for c in expected)):
                raise ValueError('Invalid declared source hash')
            if entry.get('session_id', session) != session:
                raise ValueError('Artifact session identity mismatch')
            for key in ('tool_call_id', 'producer', 'coverage'):
                if key in entry and (not isinstance(entry[key], str) or not entry[key]):
                    raise ValueError('Invalid artifact identity or coverage')
            item = {'original_relative_path': relative, 'session_id': session,
                    'declared_source_sha256': expected, 'status': 'pending',
                    'producer_declared_coverage': entry.get('coverage', 'unspecified'),
                    'verified_byte_coverage': 'unverified'}
            for key in ('tool_call_id', 'producer'):
                if key in entry:
                    item[key] = entry[key]
            records['artifacts'].append(item)
            try:
                data = _read(root_fd, source_parts, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total))
            except FileNotFoundError:
                item['status'] = 'missing'
                raise ValueError('Declared artifact is missing') from None
            digest = hashlib.sha256(data).hexdigest()
            item.update(source_sha256=digest, source_bytes=len(data))
            if digest != expected:
                item['status'] = 'hash_mismatch'
                raise ValueError('Declared artifact hash mismatch')
            total += len(data)
            stored = redact(data)
            item['output'] = write(prefix + '-' + str(index) + '.log', stored)
            item.update(status='persisted', verified_byte_coverage='declared_file_bytes',
                        redacted=stored != data)
        records['status'] = 'persisted'
        receipt()
        return records
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        records['status'] = 'failed'
        # Use a fixed public failure reason: untrusted exception text may contain secrets.
        if destination_fd is not None:
            try:
                receipt()
            except OSError:
                pass
        raise ArchiveFailure('Cannot persist declared improver artifacts', records) from None
    finally:
        for fd in (root_fd, destination_fd):
            if fd is not None:
                os.close(fd)
