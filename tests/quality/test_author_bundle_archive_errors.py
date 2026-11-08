"""Malformed authored archives are typed failures, not exception-handler NameErrors."""
import io
import tarfile

import pytest

from console import server


@pytest.mark.parametrize('kind', ['malformed', 'unsafe-member'])
def test_bad_author_archive_has_typed_error_and_never_publishes(tmp_path, monkeypatch, kind):
    root = tmp_path / 'authored'
    monkeypatch.setattr(server, 'AUTHORED_ROOT', root)
    archive = tmp_path / 'input.tar'
    if kind == 'malformed':
        archive.write_bytes(b'not an archive')
    else:
        with tarfile.open(archive, 'w') as tar:
            member = tarfile.TarInfo('../escape'); member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
    code, payload = server.author_import(archive)
    assert code == 400 and payload['error'] == 'bad-archive'
    assert not root.exists() and not (tmp_path / 'escape').exists()
