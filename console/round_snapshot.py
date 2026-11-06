"""Bounded, descriptor-relative materialization of snapshot v1 artifacts.

Compatibility: artifacts over 32 MiB JSON, 24 MiB decoded content, 10,000
files, 64 path components, 10,000 distinct path prefixes, or 2 MiB total
UTF-8 bytes across distinct prefixes are deliberately rejected even if
snapshot.load accepts them. Case-insensitive aliases are rejected for portability.

The caller must open source with O_NOFOLLOW and supply an exclusively controlled,
empty, private directory fd as destination. Neither descriptor is closed here.
On failure the destination may contain partial private output; this module never
deletes it. The caller must not expose it until this function succeeds.
"""
from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

from harnessgrad import snapshot

MAX_JSON = 32 * 1024 * 1024
MAX_CONTENT = 24 * 1024 * 1024
MAX_FILES = 10_000
MAX_DEPTH = 64
MAX_PREFIXES = 10_000
MAX_PREFIX_BYTES = 2 * 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise snapshot.SnapshotInvalid("duplicate JSON key")
        result[key] = value
    return result


def materialize(source_fd: int, destination_fd: int, *, run_id: str, round_no: int) -> dict:
    """Validate source and write to destination; return validated snapshot metadata.

    Both fds must remain private and stable for the call. Source must be a regular
    single-link file opened O_NOFOLLOW by the caller. Expected run and round are
    mandatory; mismatches fail before writing. OSError and malformed input become
    SnapshotInvalid, including disk-full errors. No rollback is attempted.
    """
    try:
        st = os.fstat(source_fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > MAX_JSON:
            raise snapshot.SnapshotInvalid("unsafe or oversized snapshot source")
        if not stat.S_ISDIR(os.fstat(destination_fd).st_mode):
            raise snapshot.SnapshotInvalid("destination is not a directory")
        if os.listdir(destination_fd):
            raise snapshot.SnapshotInvalid("destination must be empty")
        # pread avoids changing the caller's file offset, and detects file growth.
        chunks = []
        offset = 0
        while offset <= MAX_JSON:
            chunk = os.pread(source_fd, min(1024 * 1024, MAX_JSON + 1 - offset), offset)
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        if offset > MAX_JSON:
            raise snapshot.SnapshotInvalid("snapshot JSON exceeds limit")
        payload = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique)
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
            raise snapshot.SnapshotInvalid("malformed snapshot")
        files = payload["files"]
        if len(files) > MAX_FILES:
            raise snapshot.SnapshotInvalid("too many snapshot files")
        total = 0
        names = {}
        prefix_bytes = 0
        for rel, entry in files.items():
            snapshot._check_relpath(rel)
            parts = rel.split("/")
            if len(parts) > MAX_DEPTH:
                raise snapshot.SnapshotInvalid("snapshot path too deep")
            # Check every prefix, so file/directory and case-fold aliases fail.
            for i in range(1, len(parts) + 1):
                name = "/".join(parts[:i])
                folded = name.casefold()
                kind = "file" if i == len(parts) else "dir"
                previous = names.get(folded)
                if previous is not None and previous != (name, kind):
                    raise snapshot.SnapshotInvalid("snapshot path collision")
                if previous is None:
                    prefix_bytes += len(name.encode('utf-8')) + len(folded.encode('utf-8'))
                    if len(names) >= MAX_PREFIXES or prefix_bytes > MAX_PREFIX_BYTES:
                        raise snapshot.SnapshotInvalid("snapshot path prefix limit exceeded")
                    names[folded] = (name, kind)
            if not isinstance(entry, dict) or not isinstance(entry.get("content_b64"), str):
                raise snapshot.SnapshotInvalid("malformed snapshot entry")
            encoded = entry["content_b64"]
            # Upper bound before decoding, including padding slack.
            if len(encoded) > 4 * ((MAX_CONTENT + 2) // 3):
                raise snapshot.SnapshotInvalid("snapshot content exceeds limit")
            data = base64.b64decode(encoded, validate=True)
            total += len(data)
            if total > MAX_CONTENT:
                raise snapshot.SnapshotInvalid("snapshot content exceeds limit")
        snapshot._validate(payload, Path("<snapshot-fd>"))
        if payload["run_id"] != run_id or type(round_no) is not int or payload["round"] != round_no:
            raise snapshot.SnapshotInvalid("snapshot identity mismatch")

        for rel, entry in sorted(files.items()):
            parts = rel.split("/")
            parent_fd = os.dup(destination_fd)
            try:
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, 0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = child_fd
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                try:
                    data = base64.b64decode(entry["content_b64"], validate=True)
                    view = memoryview(data)
                    while view:
                        n = os.write(fd, view)
                        if n <= 0:
                            raise OSError("short write")
                        view = view[n:]
                    os.fchmod(fd, 0o755 if entry["executable"] else 0o644)
                finally:
                    os.close(fd)
            finally:
                os.close(parent_fd)
        return payload
    except snapshot.SnapshotInvalid:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise snapshot.SnapshotInvalid("cannot materialize snapshot") from exc
