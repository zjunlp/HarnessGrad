import os

import pytest

from console.round_recovery import RecoveryArtifactError, copy_state, read_snapshot
from console import round_recovery


def test_copy_state_and_reject_existing_target(tmp_path):
    run = tmp_path / 'run'
    state = run / 'state_after_round1'
    (state / 'nested').mkdir(parents=True)
    (state / 'harness.json').write_text('{}')
    (state / 'nested' / 'agent.py').write_text('print(1)')
    (state / 'ignored.pyc').write_text('ignored')
    target = tmp_path / 'target'
    copy_state(run, state.name, target)
    assert (target / 'nested' / 'agent.py').read_text() == 'print(1)'
    assert not (target / 'ignored.pyc').exists()
    with pytest.raises(FileExistsError):
        copy_state(run, state.name, target)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'oversize', 'entries'])
def test_copy_rejects_unsafe_and_cleans_partial(tmp_path, kind):
    run = tmp_path / 'run'
    state = run / 'state_after_round1'
    state.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.write_text('secret')
    if kind == 'symlink':
        (state / 'bad').symlink_to(outside)
    elif kind == 'hardlink':
        os.link(outside, state / 'bad')
    elif kind == 'fifo':
        os.mkfifo(state / 'bad')
    else:
        (state / 'bad').write_text('123456')
    target = tmp_path / 'target'
    with pytest.raises((RecoveryArtifactError, OSError)):
        copy_state(run, state.name, target, max_bytes=3 if kind == 'oversize' else 100,
                   max_files=0 if kind == 'entries' else 100)
    assert not target.exists()
    assert outside.read_text() == 'secret'


def test_source_directory_symlink_and_snapshot_limits(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (run / 'state_after_round1').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        copy_state(run, 'state_after_round1', tmp_path / 'target')
    snapshots = run / 'snapshots'
    snapshots.mkdir()
    (snapshots / 'round-1.json').write_bytes(b'1234')
    assert read_snapshot(run, 'round-1.json', max_bytes=4) == b'1234'
    with pytest.raises(RecoveryArtifactError):
        read_snapshot(run, 'round-1.json', max_bytes=3)
    (snapshots / 'round-2.json').symlink_to(snapshots / 'round-1.json')
    with pytest.raises(OSError):
        read_snapshot(run, 'round-2.json')


def test_copy_stops_scanning_at_entry_limit(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    state = run / 'state_after_round1'
    state.mkdir(parents=True)
    (state / 'a').write_text('a')
    (state / 'b').write_text('b')
    def names(_fd):
        yield 'a'
        yield 'b'
        pytest.fail('traversal continued beyond the limit')
    monkeypatch.setattr(round_recovery, '_names', names)
    with pytest.raises(RecoveryArtifactError, match='entry limit'):
        copy_state(run, state.name, tmp_path / 'target', max_files=1)


def test_unsupported_platform_fails_closed(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, 'platform', 'unsupported')
    with pytest.raises(RecoveryArtifactError, match='unsupported'):
        copy_state(tmp_path, 'state_after_round1', tmp_path / 'target')
