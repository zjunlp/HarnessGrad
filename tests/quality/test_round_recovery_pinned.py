"""Descriptor pinning and special-file race regressions (offline fixtures)."""
import os

import pytest

from console import round_recovery as recovery


def test_run_fd_survives_path_replacement(tmp_path):
    run = tmp_path / 'run'
    state = run / 'state'
    state.mkdir(parents=True)
    (state / 'file').write_text('original')
    (run / 'snapshots').mkdir()
    (run / 'snapshots' / 'round.json').write_text('original')
    dest = tmp_path / 'dest'
    dest.mkdir()
    with recovery.open_run_dir(run) as run_fd:
        run.rename(tmp_path / 'old')
        (run / 'state').mkdir(parents=True)
        (run / 'state' / 'file').write_text('replacement')
        (run / 'snapshots').mkdir()
        (run / 'snapshots' / 'round.json').write_text('replacement')
        fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY)
        try:
            recovery.copy_state_into(run, 'state', fd, run_fd=run_fd)
        finally:
            os.close(fd)
        with recovery.open_snapshot_fd(run, 'round.json', run_fd=run_fd) as source:
            assert os.read(source, 100) == b'original'
    assert (dest / 'file').read_text() == 'original'


def test_special_file_swap_never_opens_fifo_for_read(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    state = run / 'state'
    state.mkdir(parents=True)
    (state / 'file').write_text('original')
    real_open = recovery._open_regular
    def swap(name, directory_fd):
        os.unlink(name, dir_fd=directory_fd)
        os.mkfifo(name, dir_fd=directory_fd)
        return real_open(name, directory_fd)
    monkeypatch.setattr(recovery, '_open_regular', swap)
    with pytest.raises(recovery.RecoveryArtifactError):
        recovery.copy_state(run, 'state', tmp_path / 'dest')
    assert not (tmp_path / 'dest').exists()


def test_copy_into_rejects_nonempty_and_snapshot_hardlink(tmp_path):
    run = tmp_path / 'run'
    (run / 'state').mkdir(parents=True)
    (run / 'snapshots').mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('private')
    os.link(outside, run / 'snapshots' / 'round.json')
    dest = tmp_path / 'dest'
    dest.mkdir()
    (dest / 'existing').write_text('keep')
    fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(recovery.RecoveryArtifactError, match='empty'):
            recovery.copy_state_into(run, 'state', fd)
    finally:
        os.close(fd)
    with pytest.raises(recovery.RecoveryArtifactError):
        with recovery.open_snapshot_fd(run, 'round.json'):
            pass
    assert (dest / 'existing').read_text() == 'keep'
