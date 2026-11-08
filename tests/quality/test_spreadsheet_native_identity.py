"""Manifest failure controls; these are not native-engine availability tests."""
import json

import pytest

from data.scaffolds.spreadsheet_runtime.native import check, digest, require_runtime


def test_explicit_native_manifest_required(monkeypatch):
    monkeypatch.delenv('HG_SPREADSHEET_NATIVE_MANIFEST', raising=False)
    with pytest.raises(RuntimeError, match='prepare native'):
        require_runtime()


def test_recorded_native_file_change_fails_closed(tmp_path):
    artifact = tmp_path / 'identity-only-control'
    artifact.write_bytes(b'original')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'schema': 1, 'deployment': 'verified-host-installation',
                                    'soffice_binary': str(artifact), 'python': str(artifact),
                                    'program_dir': str(tmp_path),
                                    'files': {str(artifact): digest(artifact)}}))
    artifact.write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='identity changed'):
        check(manifest)


def test_native_environment_drops_credentials(monkeypatch, tmp_path):
    from data.scaffolds.spreadsheet_runtime.native import runtime_env
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-secret')
    monkeypatch.setenv('PYTHONPATH', '/actor/code')
    env = runtime_env({'program_dir': '/usr/lib/libreoffice/program'}, tmp_path)
    assert 'OPENAI_API_KEY' not in env
    assert 'PYTHONPATH' not in env
    assert env['HOME'] == str(tmp_path / 'home')


def test_bubblewrap_hides_private_inputs_and_network(tmp_path, monkeypatch):
    import shutil
    import socket
    import subprocess
    from data.scaffolds.spreadsheet_runtime.native import sandbox_command, minimal_env
    if not shutil.which('bwrap', path='/usr/bin:/bin'):
        pytest.skip('bubblewrap is not installed; native isolation unavailable')
    private = tmp_path / 'private-sentinel'
    private.write_text('private synthetic input')
    scratch = tmp_path / 'conversion'
    scratch.mkdir()
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-secret')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = f'''import os, pathlib, socket
assert 'OPENAI_API_KEY' not in os.environ
assert not pathlib.Path({str(private)!r}).exists()
for endpoint in [('127.0.0.1', {port}), ('1.1.1.1', 443)]:
    with socket.socket() as client:
        client.settimeout(0.5)
        try: client.connect(endpoint)
        except OSError: pass
        else: raise AssertionError('network escaped private namespace')
'''
        command = sandbox_command({'files': {}, 'program_dir': '/usr/lib/libreoffice/program'},
                                  scratch, [], ['/usr/bin/python3', '-I', '-c', code])
        result = subprocess.run(command, env=minimal_env(), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, 'native namespace isolation control failed'


def test_active_workbook_content_is_unmeasured(tmp_path):
    import zipfile
    from data.scaffolds.spreadsheet_runtime.native import reject_active_content
    workbook = tmp_path / 'synthetic.xlsx'
    with zipfile.ZipFile(workbook, 'w') as archive:
        archive.writestr('xl/externalLinks/externalLink1.xml', '<externalLink/>')
    with pytest.raises(RuntimeError, match='external workbook links'):
        reject_active_content(workbook)


def test_private_snapshot_checks_original_pin_without_host_global_site(tmp_path):
    """Identity-only control: no synthetic native executable is launched."""
    import base64
    import hashlib
    marker = tmp_path / 'identity-only'
    marker.write_bytes(b'identity control')
    hidden = '/synthetic-hidden/dist-packages/uno.py'
    source = b'# synthetic component identity; never executed\n'
    record = {'schema': 1, 'deployment': 'verified-host-installation',
              'soffice_binary': str(marker), 'python': str(marker),
              'program_dir': str(tmp_path),
              'files': {str(marker): digest(marker), hidden: hashlib.sha256(source).hexdigest()},
              'source_snapshots': {hidden: base64.b64encode(source).decode('ascii')}}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(record))
    before = sorted(tmp_path.iterdir())
    assert check(manifest)['files'][hidden] == record['files'][hidden]
    assert sorted(tmp_path.iterdir()) == before  # readonly, no on-check materialization
    record['source_snapshots'][hidden] = base64.b64encode(b'changed').decode('ascii')
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='identity changed'):
        check(manifest)


@pytest.mark.parametrize('path', ['/private/credentials',
                                  '/usr/lib/python3/dist-packages/../../secret/uno.py',
                                  '/usr/lib/python3/dist-packages/requests.py'])
def test_private_snapshot_cannot_authorize_other_files(path):
    from data.scaffolds.spreadsheet_runtime.native import validated_snapshots
    import base64
    record = {'files': {path: '0' * 64},
              'source_snapshots': {path: base64.b64encode(b'synthetic').decode('ascii')}}
    with pytest.raises(ValueError, match='authorized UNO'):
        validated_snapshots(record)


def test_snapshot_source_does_not_follow_links(tmp_path):
    from data.scaffolds.spreadsheet_runtime.native import snapshot_native_python
    component = tmp_path / 'dist-packages' / 'uno.py'
    component.parent.mkdir()
    payload = tmp_path / 'actual-source'
    payload.write_bytes(b'identity-only synthetic source')
    component.symlink_to(payload)
    record = {'files': {str(component): digest(payload)}}
    with pytest.raises(OSError):
        snapshot_native_python(record)
    component.unlink()
    import os
    os.link(payload, component)
    with pytest.raises(ValueError, match='regular file'):
        snapshot_native_python(record)


def test_actual_uno_snapshot_restores_only_pinned_readonly_component(tmp_path):
    """Actual native binding probe, requiring an explicitly prepared deployment."""
    import os
    import subprocess
    from data.scaffolds.spreadsheet_runtime.native import (
        snapshot_native_python, sandbox_command, minimal_env,
    )
    manifest = os.environ.get('HG_SPREADSHEET_NATIVE_MANIFEST')
    if not manifest:
        pytest.skip('explicit native deployment required for actual UNO probe')
    record = snapshot_native_python(check(manifest))
    snapshots = record.get('source_snapshots', {})
    if not snapshots:
        pytest.skip('selected UNO deployment has no masked global-site components')
    original = next(name for name in snapshots if name.endswith('/uno.py'))
    directory = str(__import__('pathlib').Path(original).parent)
    # A real undeclared system package is the sentinel; never write host global site.
    candidates = [path for path in __import__('pathlib').Path(directory).iterdir()
                  if path.name not in {__import__('pathlib').Path(name).name for name in snapshots}
                  and path.name != '__pycache__']
    assert candidates, 'control needs an existing undeclared global package sentinel'
    sentinel = str(candidates[0])
    code = f'''import pathlib, uno
assert uno.getComponentContext() is not None
assert not pathlib.Path({sentinel!r}).exists()
for path in [pathlib.Path({original!r}), *pathlib.Path('/work/native-components').iterdir()]:
 try: path.open('ab')
 except OSError: pass
 else: raise AssertionError('native snapshot writable through a mount alias')
print('actual UNO available; undeclared global sentinel absent; both aliases readonly')
'''
    result = subprocess.run(sandbox_command(record, tmp_path, [],
                            [record['python'], '-I', '-B', '-c', code]),
                            env=minimal_env(), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
