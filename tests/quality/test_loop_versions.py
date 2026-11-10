"""Bundled Loop selection preserves historical bytes without Git or downloads."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from harnessgrad.cli import main
from tools import install_harness, loop_releases as loop


def tree_sha(root):
    entries = []
    for path in sorted(root.iterdir()):
        content = path.read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).digest()
        entries.append(b'100644 ' + path.name.encode() + b'\0' + blob)
    content = b''.join(entries)
    return hashlib.sha1(b'tree ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def test_list_includes_both_loop_versions_without_node_claim(capsys):
    assert main(['harness','list']) == 0
    rows = [x for x in capsys.readouterr().out.splitlines() if x.startswith('loop ')]
    assert len(rows) == 2 and rows[0].startswith('loop 0.2.0') and rows[1].startswith('loop 0.3.0')
    assert all('Node.js' not in row for row in rows)


@pytest.mark.parametrize('version', ['0.2.0','0.3.0'])
def test_install_preserves_selected_source_and_reuses_identical_copy(tmp_path,monkeypatch,capsys,version):
    monkeypatch.setattr(install_harness,'_toolchain',Mock(side_effect=AssertionError('Loop requested Node')))
    store = tmp_path / 'store'
    assert main(['harness','install','loop','--version',version,'--install-root',str(store)]) == 0
    output = capsys.readouterr(); harness = Path(output.out.strip())
    assert json.loads((harness / 'harness.json').read_text())['version'] == version
    if version == '0.2.0':
        assert {p.name for p in harness.iterdir()} == loop.LEGACY_FILES
        assert tree_sha(harness) == loop.LEGACY_TREE
    else:
        assert (harness / 'requirements.lock').is_file()
        assert json.loads((harness / 'harness.json').read_text())['runtime']['kind'] == 'python'
    assert loop.install(version,store) == harness
    receipt = json.loads((harness.parent.parent / 'installation.json').read_text())
    assert receipt['files'] == loop._inventory(harness)
    assert 'provenance' in receipt and output.err == ''


def test_default_is_current_loop_version(tmp_path,capsys):
    assert main(['harness','install','loop','--install-root',str(tmp_path / 'store')]) == 0
    output = capsys.readouterr()
    assert json.loads((Path(output.out.strip()) / 'harness.json').read_text())['version'] == '0.3.0'
    assert 'version 0.3.0' in output.err


@pytest.mark.parametrize('version', ['latest','0.1.0','../0.2.0','0.2.0/other'])
def test_unknown_loop_version_creates_nothing(tmp_path,capsys,version):
    store = tmp_path / 'store'
    assert main(['harness','install','loop','--version',version,'--install-root',str(store)]) == 1
    assert not store.exists() and 'Loop version must be one of' in capsys.readouterr().err


@pytest.mark.parametrize('kind', ['modified','extra-file','symlink'])
def test_changed_installation_is_preserved_and_refused(tmp_path,kind):
    harness = loop.install('0.2.0',tmp_path / 'store')
    agent = harness / 'agent.py'
    if kind == 'modified': agent.write_text('user edits')
    elif kind == 'extra-file': (harness / 'custom.py').write_text('user edits')
    else:
        agent.unlink();agent.symlink_to(tmp_path / 'external.py')
        (tmp_path / 'external.py').write_text('user edits')
    with pytest.raises(ValueError,match='changed|link'):
        loop.install('0.2.0',tmp_path / 'store')
    assert (agent.read_text() if kind != 'extra-file' else (harness / 'custom.py').read_text()) == 'user edits'


def test_archive_digest_failure_does_not_publish(tmp_path,monkeypatch):
    archive = tmp_path / 'corrupt.zip';archive.write_bytes(b'not the source')
    monkeypatch.setattr(loop,'ARCHIVE',archive)
    with pytest.raises(ValueError,match='archive digest mismatch'):
        loop.install('0.2.0',tmp_path / 'store')
    assert not (tmp_path / 'store/loop/0.2.0/installation.json').exists()


def test_release_symlink_is_refused(tmp_path):
    store = tmp_path / 'store';store.mkdir()
    outside = tmp_path / 'outside';outside.mkdir()
    (store / 'loop').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlinks'):
        loop.install('0.2.0',store)
    assert not list(outside.iterdir())
