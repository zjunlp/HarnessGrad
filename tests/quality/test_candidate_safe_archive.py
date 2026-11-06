import io
import os
import tarfile

import pytest

from console import server, submissions


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / 'candidates'
    root.mkdir()
    work = root / 'test'
    work.mkdir()
    (work / 'harness.json').write_text('{}')
    monkeypatch.setattr(server, 'CANDIDATES', root)
    return work


def test_archive_admitted_and_modes_and_exclusions(candidate, tmp_path):
    tool = candidate / 'run.sh'
    tool.write_text('#!/bin/sh\nexit 0\n')
    tool.chmod(0o755)
    (candidate / 'large').write_bytes(b'A' * (2 * 1024 * 1024 + 123))
    for directory in ('.git', '.state', '__pycache__'):
        (candidate / directory).mkdir()
        (candidate / directory / 'secret').write_text('secret')
    (candidate / 'secret.pyc').write_text('secret')
    status, blob = server.harness_candidate_archive('test')
    assert status == 200
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tar:
        assert set(tar.getnames()) == {'harness.json', 'run.sh', 'large'}
        assert tar.getmember('run.sh').mode == 0o755
        assert len(tar.extractfile('large').read()) == 2 * 1024 * 1024 + 123
    archive = tmp_path / 'candidate.tar.gz'
    archive.write_bytes(blob)
    assert submissions.inspect_archive(archive) == []
    extracted = tmp_path / 'extracted'
    extracted.mkdir()
    submissions._scan_archive(archive, extracted)
    assert (extracted / 'run.sh').stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize('name', ['.', '..', '../test', '/test'])
def test_invalid_candidate_names(candidate, name):
    assert server.harness_candidate_archive(name) == (404, None)


def test_missing_manifest_is_not_found(candidate):
    (candidate / 'harness.json').unlink()
    assert server.harness_candidate_archive('test') == (404, None)


def test_manifest_removed_after_precheck(candidate, monkeypatch):
    original = os.scandir

    def remove_before_traversal(path):
        (candidate / 'harness.json').unlink()
        return original(path)

    monkeypatch.setattr(os, 'scandir', remove_before_traversal)
    assert server.harness_candidate_archive('test') == (400, None)


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'hardlink'])
def test_unsafe_nested_objects(candidate, tmp_path, kind):
    nested = candidate / 'nested'
    nested.mkdir()
    secret = tmp_path / 'secret'
    secret.write_text('outside secret')
    path = nested / 'unsafe'
    if kind == 'symlink':
        path.symlink_to(secret)
    elif kind == 'fifo':
        os.mkfifo(path)
    else:
        os.link(secret, path)
    assert server.harness_candidate_archive('test') == (400, None)


def test_root_and_candidate_symlink_rejected(candidate, tmp_path, monkeypatch):
    (candidate.parent / 'link').symlink_to(candidate, target_is_directory=True)
    assert server.harness_candidate_archive('link') == (400, None)
    link = tmp_path / 'root-link'
    link.symlink_to(candidate.parent, target_is_directory=True)
    monkeypatch.setattr(server, 'CANDIDATES', link)
    assert server.harness_candidate_archive('test') == (400, None)


def test_entry_limit(candidate):
    for i in range(2000):
        (candidate / str(i)).touch()
    assert server.harness_candidate_archive('test') == (413, None)


def test_compressed_and_expanded_caps(candidate, monkeypatch):
    (candidate / 'data').write_bytes(os.urandom(100_000))
    monkeypatch.setattr(submissions, 'MAX_PAYLOAD', 10_000)
    assert server.harness_candidate_archive('test') == (413, None)
    monkeypatch.setattr(submissions, 'MAX_PAYLOAD', 200_000)
    monkeypatch.setattr(submissions, 'MAX_EXPANDED', 99_999)
    assert server.harness_candidate_archive('test') == (413, None)


def test_incompatible_alias_and_path(candidate):
    (candidate / 'Case').touch()
    (candidate / 'case').touch()
    assert server.harness_candidate_archive('test') == (400, None)
    (candidate / 'case').unlink()
    (candidate / 'bad:name').touch()
    assert server.harness_candidate_archive('test') == (400, None)


def test_deep_tree(candidate):
    path = candidate
    for _ in range(150):
        path = path / 'd'
        path.mkdir()
    (path / 'file').write_text('ok')
    status, blob = server.harness_candidate_archive('test')
    assert status == 200
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tar:
        assert tar.extractfile('/'.join(['d'] * 150 + ['file'])).read() == b'ok'


@pytest.mark.parametrize('status, expected', [(400, 'invalid or unsafe'), (413, 'exceeds'), (404, 'not found')])
def test_http_archive_errors(monkeypatch, status, expected):
    monkeypatch.setattr(server, 'harness_candidate_archive', lambda name: (status, None))
    handler = object.__new__(server.Handler)
    handler.path = '/api/harness/archive?candidate=test'
    handler._api_guard = lambda: True
    sent = []
    handler._error = lambda message, code: sent.append((message, code))
    handler.do_GET()
    assert sent[0][1] == status
    assert expected in sent[0][0]
