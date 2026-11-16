"""Offline CLI persistence and boundary regression tests."""
import hashlib
import os
from pathlib import Path
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'methods'))
from improver_archive import ArchiveFailure, TranscriptArchive, file_record, run_archived


def invoke(tmp_path, script, timeout=5, credentials=()):
    return run_archived([sys.executable, '-c', script], timeout=timeout,
                        cwd=tmp_path, env=dict(os.environ), directory=tmp_path,
                        credentials=credentials)


def test_full_unicode_head_tail_and_unique_files(tmp_path):
    text = 'usage: 918 tokens\n' + '汉字abc' * 100_000
    proc, first = invoke(tmp_path, "import sys; sys.stdout.write('usage: 918 tokens\\n' + '汉字abc' * 100_000)")
    full = Path(first['full']['path'])
    assert full.read_bytes() == text.encode()
    assert first['raw_bytes'] == len(text.encode())
    assert {k: first['full'][k] for k in ('path', 'bytes', 'sha256')} == file_record(full)
    assert first['raw_chars'] == len(text) == first['full']['chars']
    assert first['raw_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert proc.stdout == text
    assert Path(first['tail']['path']).stat().st_size <= 200_000
    assert first['complete'] and first['native_body_coverage'] == 'not_exported'
    _, second = invoke(tmp_path, "print('second call')")
    assert first['full']['path'] != second['full']['path']
    assert full.exists()
    assert stat.S_IMODE(full.stat().st_mode) == 0o600
    assert all(p.suffix == '.log' for p in tmp_path.iterdir())


def test_timeout_retains_partial_and_nonzero_is_complete(tmp_path):
    proc, records = invoke(tmp_path, "import time; print('partial evidence', flush=True); time.sleep(20)", .2)
    assert proc.returncode == -1
    assert records['outcome'] == 'timeout' and not records['complete']
    assert Path(records['full']['path']).read_text() == 'partial evidence\n'
    proc, records = invoke(tmp_path, "import sys; print('failed evidence'); sys.exit(3)")
    assert proc.returncode == 3 and records['complete']
    assert records['outcome'] == 'nonzero_exit'


def test_redaction_across_every_chunk_boundary(tmp_path):
    secret = 'private-key-汉字'
    archive = TranscriptArchive(tmp_path, [secret])
    raw = ('before ' + secret + ' after ' + secret).encode()
    for byte in raw:
        archive.feed(bytes([byte]))
    records = archive.finish(complete=True, outcome='success')
    assert Path(records['full']['path']).read_bytes() == b'before [REDACTED] after [REDACTED]'
    assert records['raw_sha256'] == hashlib.sha256(raw).hexdigest()
    assert records['raw_sha256'] != records['full']['sha256']
    assert secret.encode() not in Path(records['tail']['path']).read_bytes()


def test_symlink_directory_and_file_rejected(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ArchiveFailure) as caught:
        TranscriptArchive(alias)
    assert caught.value.retryable is False
    target = real / 'original.log'
    target.write_text('evidence')
    alias_file = real / 'alias.log'
    alias_file.symlink_to(target)
    with pytest.raises(ArchiveFailure):
        file_record(alias_file)


def test_launch_exception_retains_receipt(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_archived(['/does/not/exist'], timeout=1, cwd=tmp_path,
                     env={}, directory=tmp_path)
    receipts = list(tmp_path.glob('*receipt.log'))
    assert len(receipts) == 3
    assert all('exception' in receipt.read_text() for receipt in receipts)


def test_eof_does_not_disable_deadline(tmp_path):
    proc, records = invoke(tmp_path,
        "import os,time; os.close(1); os.close(2); time.sleep(20)", .2)
    assert proc.returncode == -1 and records['outcome'] == 'timeout'


def test_archive_survives_candidate_namespace_cleanup(tmp_path):
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    host_archive = tmp_path / 'host'
    host_archive.mkdir()
    proc, records = run_archived([sys.executable, '-c', "print('durable host evidence')"],
        timeout=5, cwd=candidate, env={}, directory=host_archive)
    candidate.rmdir()
    assert Path(records['full']['path']).read_text() == 'durable host evidence\n'


def test_write_failure_is_nonretryable_and_preserves_created_file(tmp_path, monkeypatch):
    archive = TranscriptArchive(tmp_path)
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(os, 'write', fail)
    with pytest.raises(ArchiveFailure) as caught:
        archive.feed(b'evidence')
    assert caught.value.terminal_stop['code'] == 'artifact_persistence_failed'
    assert list(tmp_path.glob('*full.log'))
    os.close(archive.fd)
    os.close(archive.dir_fd)


def test_logical_order_and_summary_match_legacy_subprocess(tmp_path):
    import subprocess
    from methods.cli_improver import _summary_of
    code = ("import sys,time; "
        "sys.stderr.write('The stderr summary describes the mechanism examined during this attempt.\\r\\n'); "
        "sys.stderr.flush(); time.sleep(.03); "
        "sys.stdout.write('dsh-usage {\"input_tokens\":12,\"output_tokens\":3,\"total_tokens\":15}\\n'); "
        "sys.stdout.write('The stdout summary arrived last in time but belongs before stderr.\\n'); "
        "sys.stdout.flush()")
    legacy = subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True)
    proc, records = invoke(tmp_path,code)
    expected = legacy.stdout + legacy.stderr
    assert proc.stdout == expected
    assert _summary_of(proc.stdout) == _summary_of(expected)
    assert _summary_of(proc.stdout).startswith('The stderr summary')
    assert records['coverage'] == 'cli_stdout_then_stderr'
    assert Path(records['streams']['stdout']['full']['path']).read_text() == legacy.stdout
    assert Path(records['streams']['stderr']['full']['path']).read_text() == legacy.stderr


def test_timeout_preserves_both_partial_streams_and_original_hashes(tmp_path):
    proc, records = invoke(tmp_path,
        "import os,time; os.write(2,b'stderr partial\\n'); os.write(1,b'stdout partial\\n'); time.sleep(20)", .2)
    assert proc.returncode == -1 and not records['complete']
    assert proc.stdout == 'stdout partial\nstderr partial\n'
    for name in ('stdout','stderr'):
        stream = records['streams'][name]
        expected = (name + ' partial\n').encode()
        assert Path(stream['full']['path']).read_bytes() == expected
        assert stream['raw_sha256'] == hashlib.sha256(expected).hexdigest()
        assert stream['raw_bytes'] == len(expected)
        assert stream['complete'] is False
        assert '-partial.log' in stream['full']['path']


def test_concurrent_pipes_and_per_stream_redaction_hashes(tmp_path):
    secret = 'synthetic-selected-private-key'
    code = ("import os; "
            "os.write(2," + repr((secret+'\n').encode()) + "); "
            "[(os.write(1,b'A'*65536),os.write(2,b'B'*65536)) for _ in range(4)]")
    proc, records = invoke(tmp_path,code,credentials=[secret])
    stdout = b'A' * (65536*4)
    stderr = (secret+'\n').encode() + b'B'*(65536*4)
    assert records['streams']['stdout']['raw_sha256'] == hashlib.sha256(stdout).hexdigest()
    assert records['streams']['stderr']['raw_sha256'] == hashlib.sha256(stderr).hexdigest()
    assert records['raw_sha256'] is None
    assert records['raw_bytes'] == len(stdout) + len(stderr)
    assert secret not in proc.stdout
    assert proc.stdout.startswith('A'*(65536*4))
    assert all(secret.encode() not in file.read_bytes() for file in tmp_path.glob('*.log'))


def test_interruption_retains_independently_marked_partial_streams(tmp_path,monkeypatch):
    import json
    original = TranscriptArchive.feed
    interrupted = []
    def feed(self,data,final=False):
        original(self,data,final)
        if data and self.coverage == 'cli_stderr' and not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt('synthetic capture interruption')
    monkeypatch.setattr(TranscriptArchive,'feed',feed)
    with pytest.raises(KeyboardInterrupt):
        invoke(tmp_path,"import os,time; os.write(1,b'out'); os.write(2,b'err'); time.sleep(20)")
    streams = list(tmp_path.glob('*-partial.log'))
    assert len(streams) == 2
    receipts = [json.loads(file.read_text()) for file in tmp_path.glob('*receipt.log')]
    independent = [row for row in receipts if row['coverage'] in ('cli_stdout','cli_stderr')]
    assert len(independent) == 2 and all(not row['complete'] for row in independent)
    assert any(file.read_bytes() == b'err' for file in streams)
