import hashlib
import json
import os
from pathlib import Path

import pytest

from methods.improver_archive import ArchiveFailure
from methods.improver_artifacts import persist_artifact_manifest


def declare(root, path='body.json', body=b'{"body":"hello"}', session='session-1', **extra):
    target = root / path
    target.write_bytes(body)
    manifest = root / 'artifact-manifest.json'
    manifest.write_text(json.dumps({'version': 1, 'session_id': session, 'artifacts': [
        {'path': path, 'sha256': hashlib.sha256(body).hexdigest(), 'tool_call_id': 'call-1', **extra}]}))
    return manifest


def test_private_copy_survives_source_deletion_and_session_isolation(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    output = tmp_path / 'output'
    output.mkdir()
    manifest = declare(root)
    first = persist_artifact_manifest(manifest, allowed_root=root, destination=output)
    second = persist_artifact_manifest(manifest, allowed_root=root, destination=output)
    assert first['manifest']['path'] != second['manifest']['path']
    (root / 'body.json').unlink()
    assert Path(first['artifacts'][0]['output']['path']).read_bytes() == b'{"body":"hello"}'
    assert first['artifacts'][0]['tool_call_id'] == 'call-1'
    for path in output.iterdir():
        assert path.suffix == '.log'
        assert path.stat().st_mode & 0o777 == 0o600


def test_credentials_redacted_with_distinct_hashes(tmp_path):
    manifest = declare(tmp_path, body=b'before secret-value after')
    result = persist_artifact_manifest(manifest, allowed_root=tmp_path, destination=tmp_path,
                                       credentials=('secret-value',))
    item = result['artifacts'][0]
    assert item['source_sha256'] != item['output']['sha256']
    for path in tmp_path.glob('*.log'):
        assert b'secret-value' not in path.read_bytes()


@pytest.mark.parametrize('failure', ['traversal', 'absolute', 'symlink', 'hardlink', 'fifo', 'hash', 'missing', 'session', 'duplicate'])
def test_reject_unsafe_or_inconsistent_declarations(tmp_path, failure):
    manifest = declare(tmp_path)
    declaration = json.loads(manifest.read_text())
    entry = declaration['artifacts'][0]
    body = tmp_path / 'body.json'
    if failure == 'traversal':
        entry['path'] = '../body.json'
    elif failure == 'absolute':
        entry['path'] = str(body)
    elif failure == 'symlink':
        body.unlink()
        body.symlink_to(manifest)
    elif failure == 'hardlink':
        os.link(body, tmp_path / 'alias')
    elif failure == 'fifo':
        body.unlink()
        os.mkfifo(body)
    elif failure == 'hash':
        body.write_bytes(b'changed')
    elif failure == 'missing':
        body.unlink()
    elif failure == 'session':
        entry['session_id'] = 'other'
    else:
        declaration['artifacts'].append(dict(entry))
    manifest.write_text(json.dumps(declaration))
    with pytest.raises(ArchiveFailure) as error:
        persist_artifact_manifest(manifest, allowed_root=tmp_path, destination=tmp_path)
    assert error.value.retryable is False
    assert error.value.records['status'] == 'failed'
    assert Path(error.value.records['manifest']['path']).is_file()
    if failure == 'missing':
        assert error.value.records['artifacts'][0]['status'] == 'missing'


def test_absent_manifest_does_not_infer_bodies(tmp_path):
    (tmp_path / 'native-body.json').write_text('body')
    result = persist_artifact_manifest(tmp_path / 'absent.json', allowed_root=tmp_path, destination=tmp_path)
    assert result['status'] == 'not_declared'
    assert result['native_body_coverage'] == 'not_declared'
    assert result['artifacts'] == []
