"""Private, streaming CLI evidence archives (observed output, not native API bodies)."""
from __future__ import annotations

import codecs
import hashlib
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import stat
import subprocess
import time


class ArchiveFailure(RuntimeError):
    """Evidence persistence failed; callers must not retry the model invocation."""
    retryable = False
    terminal_stop = {"schema": "harnessgrad.terminal-stop.v1",
                     "code": "artifact_persistence_failed", "retryable": False,
                     "scope": "meta_session"}

    def __init__(self, message, records=None):
        super().__init__(message)
        self.observed_transcript = None
        self.capture_complete = False
        self.transcript_archive = None
        self.records = records or {}
        self.detail = {"type": "archive_failure", "retryable": False,
                       "message": message, "archive": self.records}


def _directory(path):
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        return path, fd
    except BaseException:
        os.close(fd)
        raise


def file_record(path):
    """Describe an existing private regular archive without following a final symlink."""
    original = Path(path)
    directory, directory_fd = _directory(original.parent)
    try:
        fd = os.open(original.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ArchiveFailure('Archive is not a regular file')
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
                size += len(chunk)
        return {"path": str(directory / original.name), "bytes": size,
                "sha256": digest.hexdigest()}
    except OSError as exc:
        raise ArchiveFailure('Cannot read CLI archive') from exc
    finally:
        os.close(directory_fd)


class TranscriptArchive:
    """One call's full redacted stream, bounded tail, and machine-readable receipt."""
    def __init__(self, directory, credentials=(), tail_bytes=200_000,
                 coverage="provided_transcript_bytes", raw_representation="subprocess_bytes", stream_name=None):
        self.coverage = coverage
        self.raw_representation = raw_representation
        self.records = {}
        self.fd = None
        self.dir_fd = None
        try:
            self.directory, self.dir_fd = _directory(directory)
            self.prefix = 'improver-' + secrets.token_hex(16)
            self.name = self.prefix + ('-' + stream_name + '-partial.log'
                                       if stream_name else '-full.log')
            self.fd = self._open(self.name)
        except OSError as exc:
            if self.dir_fd is not None:
                os.close(self.dir_fd)
            raise ArchiveFailure('Cannot create private CLI archive') from exc
        self.secrets = sorted({str(x).encode() for x in credentials if x}, key=len, reverse=True)
        self.redaction_matches = 0
        self.pending = b''
        self.tail = b''
        self.tail_limit = tail_bytes
        self.raw_hash = hashlib.sha256()
        self.written_hash = hashlib.sha256()
        self.raw_bytes = self.written_bytes = 0
        self.raw_chars = self.written_chars = 0
        self.raw_decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.written_decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

    def _open(self, name):
        return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                       0o600, dir_fd=self.dir_fd)

    def _write(self, data):
        view = memoryview(data)
        while view:
            count = os.write(self.fd, view)
            if count <= 0:
                raise OSError('short archive write')
            view = view[count:]
        os.fsync(self.fd)
        self.written_hash.update(data)
        self.written_bytes += len(data)
        self.written_chars += len(self.written_decoder.decode(data))
        self.tail = (self.tail + data)[-self.tail_limit:]

    def feed(self, data, final=False):
        try:
            self.raw_hash.update(data)
            self.raw_bytes += len(data)
            self.raw_chars += len(self.raw_decoder.decode(data, final=final))
            self.pending += data
            if not self.secrets:
                self._write(self.pending)
                self.pending = b''
                return
            # Consume only positions with enough lookahead to match every credential.
            stop = len(self.pending) if final else max(0, len(self.pending) - max(map(len, self.secrets), default=1) + 1)
            pos = 0
            output = bytearray()
            while pos < stop:
                match = next((s for s in self.secrets if self.pending.startswith(s, pos)), None)
                if match:
                    self.redaction_matches += 1
                    output.extend(b'[REDACTED]')
                    pos += len(match)
                else:
                    output.append(self.pending[pos])
                    pos += 1
            self.pending = self.pending[pos:]
            self._write(bytes(output))
        except OSError as exc:
            raise ArchiveFailure('Cannot persist CLI output', self.records) from exc

    def finish(self, *, complete, outcome, extra_records=None):
        try:
            self.feed(b'', final=True)
            self.written_chars += len(self.written_decoder.decode(b'', final=True))
            os.fsync(self.fd)
            os.close(self.fd)
            self.fd = None
            self.records = {"full": {"path": str(self.directory / self.name),
                "bytes": self.written_bytes, "chars": self.written_chars, "sha256": self.written_hash.hexdigest()},
                "raw_bytes": self.raw_bytes, "raw_chars": self.raw_chars, "raw_sha256": self.raw_hash.hexdigest(),
                "coverage": self.coverage, "raw_representation": self.raw_representation,
                "complete": complete,
                "outcome": outcome, "redacted": bool(self.redaction_matches),
                "native_body_coverage": "not_exported", "upstream_transcript_complete": None}
            if extra_records:
                self.records.update(extra_records)
            for suffix in ('tail', 'receipt'):
                content = self.tail if suffix == 'tail' else json.dumps(self.records, sort_keys=True).encode()
                name = self.prefix + '-' + suffix + '.log'
                fd = self._open(name)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.records[suffix] = {"path": str(self.directory / name), "bytes": len(content),
                                        "sha256": hashlib.sha256(content).hexdigest()}
            os.fsync(self.dir_fd)
            return self.records
        except OSError as exc:
            raise ArchiveFailure('Cannot finalize CLI archive', self.records) from exc
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            os.close(self.dir_fd)
            self.dir_fd = None


def _read_archived_bytes(path):
    path = Path(path)
    _, directory_fd = _directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ArchiveFailure('CLI stream is not a private regular file')
            return stream.read()
    finally:
        os.close(directory_fd)


def _legacy_text(data):
    # subprocess.run(text=True) decodes each pipe independently and normalizes newlines.
    return data.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')


def run_archived(argv, *, timeout, cwd, env, directory, credentials=()):
    """Drain both pipes durably, then reproduce legacy stdout + stderr ordering.

    Individually named *-stdout-partial.log / *-stderr-partial.log files remain
    readable if finalization is interrupted. Only their final receipts establish
    completeness; absence of a receipt always means incomplete capture.
    """
    streams = {}
    try:
        for name in ('stdout', 'stderr'):
            streams[name] = TranscriptArchive(directory, credentials,
                coverage='cli_' + name, stream_name=name)
    except BaseException:
        for archive in streams.values():
            archive.finish(complete=False, outcome='archive_setup_failure')
        raise
    proc = None
    complete = False
    outcome = 'exception'
    failure = None
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd, env=env, start_new_session=True)
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    outcome = 'timeout'
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    deadline = float('inf')
                for key, _ in selector.select(min(remaining if remaining > 0 else 1, 0.1)):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if data:
                        streams[key.data].feed(data)
                    else:
                        selector.unregister(key.fileobj)
            try:
                proc.wait(timeout=max(0, deadline - time.monotonic()) if outcome != 'timeout' else None)
            except subprocess.TimeoutExpired:
                outcome = 'timeout'
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
        complete = outcome != 'timeout'
        if complete:
            outcome = 'success' if proc.returncode == 0 else 'nonzero_exit'
    except BaseException as exc:
        failure = exc
    finally:
        if proc is not None:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()
    captured = {}
    for name, archive in streams.items():
        try:
            captured[name] = archive.finish(complete=complete, outcome=outcome)
        except BaseException as exc:
            failure = exc
    records = {'streams': captured, 'complete': False, 'coverage': 'cli_stdout_then_stderr'}
    transcript = ''.join(_legacy_text(archive.tail) for archive in streams.values())
    transcript_loaded = False
    logical = None
    try:
        if len(captured) == 2:
            bodies = [_read_archived_bytes(captured[name]['full']['path'])
                      for name in ('stdout', 'stderr')]
            original_hash = None
            if not any(row['redacted'] for row in captured.values()):
                digest = hashlib.sha256()
                for body in bodies:
                    digest.update(body)
                original_hash = digest.hexdigest()
            logical = TranscriptArchive(directory, credentials,
                coverage='cli_stdout_then_stderr',
                raw_representation='subprocess_bytes_stdout_then_stderr')
            logical.feed(''.join(_legacy_text(body) for body in bodies).encode('utf-8'))
            records = logical.finish(complete=complete, outcome=outcome, extra_records={
                'streams': captured,
                'raw_bytes': sum(row['raw_bytes'] for row in captured.values()),
                'raw_chars': sum(row['raw_chars'] for row in captured.values()),
                'raw_sha256': original_hash,
                'raw_sha256_coverage': ('stdout_then_stderr_original_bytes' if original_hash
                                        else 'unknown_after_stream_redaction'),
                'redacted': bool(logical.redaction_matches or
                                 any(row['redacted'] for row in captured.values()))})
            transcript = _read_archived_bytes(records['full']['path']).decode('utf-8', errors='replace')
            transcript_loaded = True
    except BaseException as exc:
        failure = exc
        if logical is not None:
            if logical.fd is not None:
                os.close(logical.fd)
                logical.fd = None
            if logical.dir_fd is not None:
                os.close(logical.dir_fd)
                logical.dir_fd = None
    for credential in sorted({str(value) for value in credentials if value}, key=len, reverse=True):
        transcript = transcript.replace(credential, '[REDACTED]')
    if failure is not None:
        if not isinstance(failure, Exception):
            raise failure
        if proc is None and not isinstance(failure, ArchiveFailure):
            raise failure
        error = failure if isinstance(failure, ArchiveFailure) else ArchiveFailure(
            'Cannot collect started CLI output', records)
        error.execution_started = proc is not None
        error.observed_transcript = transcript
        error.capture_complete = bool(records.get('complete', False) and transcript_loaded)
        error.transcript_archive = records
        if error is failure:
            raise error
        raise error from failure
    return subprocess.CompletedProcess(argv, -1 if outcome == 'timeout' else proc.returncode,
                                       transcript, ''), records
