"""Authored controls for restoring private files without changing actor outputs."""
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from eval.observe.tracing import _materialize
from eval.verification.verifier import _verify


@pytest.mark.parametrize('from_file', [False, True])
@pytest.mark.parametrize('sandbox', [False, True])
def test_private_restoration_keeps_answer_bytes_and_measured_zero(tmp_path, from_file, sandbox):
    work = tmp_path / 'work'
    work.mkdir()
    answer = work / 'answer.txt'
    answer.write_text('authored wrong answer')
    os.link(answer, work / 'reference.txt')
    source = tmp_path / 'reference-source.txt'
    source.write_text('authored reference')
    entry = {'path': 'reference.txt',
             **({'from': str(source)} if from_file else {'content': source.read_text()})}
    checker = (
        "import json;from pathlib import Path;"
        "reward=int(Path('answer.txt').read_text()==Path('reference.txt').read_text());"
        "Path('verifier/reward.txt').write_text(str(reward));"
        "Path('verifier/evidence.json').write_text(json.dumps({'measured':True}))"
    )
    verifier = {'kind': 'command', 'argv': [sys.executable, '-I', '-B', '-c', checker],
                'inputs': [entry], 'reward_file': '/verifier/reward.txt',
                'evidence_file': '/verifier/evidence.json',
                'result_contract': {'version': 1, 'score_range': [0, 1],
                                    'require_exit_zero': True}}
    verdict = _verify(work, verifier, '', sandbox=sandbox, timeout_s=15,
                      env={'kind': 'files'})
    assert verdict['score'] == 0
    assert verdict['evidence']['measured'] is True
    assert answer.read_text() == 'authored wrong answer'
    assert (work / 'reference.txt').read_text() == 'authored reference'
    assert not os.path.samefile(answer, work / 'reference.txt')
    assert 'authored reference' not in json.dumps(verdict)


def test_restoring_readonly_file_preserves_source_mode_and_other_links(tmp_path):
    source = tmp_path / 'source'
    source.write_text('trusted script')
    source.chmod(0o555)
    work = tmp_path / 'work'
    work.mkdir()
    original = work / 'original'
    original.write_text('actor output')
    original.chmod(0o444)
    os.link(original, work / 'check.py')
    _materialize(work, [{'path': 'check.py', 'from': str(source)}], tmp_path)
    assert original.read_text() == 'actor output'
    assert stat.S_IMODE(original.stat().st_mode) == 0o444
    assert (work / 'check.py').read_text() == source.read_text()
    assert stat.S_IMODE((work / 'check.py').stat().st_mode) == 0o555


@pytest.mark.parametrize('parent', [False, True])
def test_symbolic_destination_cannot_receive_private_bytes(tmp_path, parent):
    work = tmp_path / 'work'
    work.mkdir()
    sibling = work / 'actor-data'
    sibling.mkdir()
    retained = sibling / 'reference.txt'
    retained.write_text('retain original')
    if parent:
        (work / 'private').symlink_to(sibling, target_is_directory=True)
        destination = 'private/reference.txt'
    else:
        (work / 'reference.txt').symlink_to(retained)
        destination = 'reference.txt'
    with pytest.raises(ValueError, match='symbolic link'):
        _materialize(work, [{'path': destination, 'content': 'authored private bytes'}], tmp_path)
    assert retained.read_text() == 'retain original'


def test_directory_restoration_does_not_chmod_linked_external_tree(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'check.py').write_text('trusted')
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    retained = outside / 'retained'
    retained.write_text('unchanged')
    work = tmp_path / 'work'
    target = work / 'checks'
    target.mkdir(parents=True)
    (target / 'link').symlink_to(outside, target_is_directory=True)
    _materialize(work, [{'path': 'checks', 'from': str(source)}], tmp_path)
    assert retained.read_text() == 'unchanged'
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    assert (target / 'check.py').read_text() == 'trusted'


def test_failed_file_copy_preserves_existing_artifact(tmp_path, monkeypatch):
    from eval.observe import tracing
    work = tmp_path / 'work'
    work.mkdir()
    target = work / 'check.py'
    target.write_text('previous artifact')
    source = tmp_path / 'source'
    source.write_text('trusted')

    def failed_copy(*args, **kwargs):
        raise OSError('authored interrupted copy')

    monkeypatch.setattr(tracing.shutil, 'copy2', failed_copy)
    with pytest.raises(OSError, match='interrupted copy'):
        _materialize(work, [{'path': 'check.py', 'from': str(source)}], tmp_path)
    assert target.read_text() == 'previous artifact'
    assert list(work.iterdir()) == [target]
