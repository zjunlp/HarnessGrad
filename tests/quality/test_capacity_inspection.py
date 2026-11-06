"""Synthetic read-only capacity checks and both console admission paths."""
import errno
import fcntl
import os

import pytest

from harnessgrad import slots
from console import submissions
from test_console_launch_preflight import _console, _form, _prepare


@pytest.mark.parametrize('capacity,stamp', [(1, b'1'), (1, b'1\n'), (2, b'2'), (2, b'2\n')])
def test_fresh_and_canonical_are_read_only(tmp_path, capacity, stamp):
    missing = tmp_path / 'missing'
    slots.inspect_capacity(missing, capacity)
    assert not missing.exists()
    (tmp_path / 'capacity').write_bytes(stamp)
    (tmp_path / 'capacity.lock').write_bytes(b'')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    slots.inspect_capacity(tmp_path, capacity)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('stamp', [b'', b' 1', b'1 ', b'1\n\n', b'01', b'\xff', b'0'])
def test_invalid_stamp_rejected_without_writes(tmp_path, stamp):
    (tmp_path / 'capacity').write_bytes(stamp)
    with pytest.raises(slots.SlotBudgetError, match='invalid capacity stamp'):
        slots.inspect_capacity(tmp_path, 1)
    assert [(p.name, p.read_bytes()) for p in tmp_path.iterdir()] == [('capacity', stamp)]


def test_mismatch_and_partial_marker(tmp_path):
    (tmp_path / 'capacity').write_bytes(b'2\n')
    with pytest.raises(slots.SlotBudgetError, match='already has capacity 2'):
        slots.inspect_capacity(tmp_path, 1)
    (tmp_path / 'capacity.lock').write_bytes(b'i')
    with pytest.raises(slots.SlotBudgetError, match='incomplete capacity'):
        slots.inspect_capacity(tmp_path, 2)
    (tmp_path / 'capacity').unlink()
    with pytest.raises(slots.SlotBudgetError, match='incomplete capacity'):
        slots.inspect_capacity(tmp_path, 2)
    assert (tmp_path / 'capacity.lock').read_bytes() == b'i'


def test_busy_initializer_is_nonblocking(tmp_path):
    lock = tmp_path / 'capacity.lock'
    lock.write_bytes(b'')
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with pytest.raises(slots.SlotBudgetError, match='initialization in progress'):
            slots.inspect_capacity(tmp_path, 1)
    finally:
        os.close(fd)
    assert lock.read_bytes() == b''


def test_unreadable_fails_closed(tmp_path, monkeypatch):
    original = slots.os.open
    def denied(path, *args, **kwargs):
        if str(path).endswith('capacity.lock'):
            raise PermissionError(errno.EACCES, 'denied')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(slots.os, 'open', denied)
    with pytest.raises(slots.SlotBudgetError, match='cannot inspect'):
        slots.inspect_capacity(tmp_path, 1)
    assert list(tmp_path.iterdir()) == []


def test_unreadable_stamp_fails_closed(tmp_path, monkeypatch):
    from pathlib import Path
    (tmp_path / 'capacity').write_bytes(b'1')
    original = Path.read_bytes
    def denied(path):
        if path.name == 'capacity':
            raise PermissionError(errno.EACCES, 'denied')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', denied)
    with pytest.raises(slots.SlotBudgetError, match='cannot inspect'):
        slots.inspect_capacity(tmp_path, 1)


@pytest.mark.parametrize('stamp,marker,detail', [
    (b'1 ', b'', 'invalid capacity'), (b'', b'', 'invalid capacity'),
    (b'1\n', b'i', 'incomplete capacity'), (b'2\n', b'', 'already has capacity')])
def test_both_admission_paths_agree(tmp_path, monkeypatch, stamp, marker, detail):
    budget = tmp_path / 'budget'
    budget.mkdir()
    (budget / 'capacity').write_bytes(stamp)
    (budget / 'capacity.lock').write_bytes(marker)
    module = _console()
    _prepare(module, monkeypatch, budget)
    monkeypatch.setattr(module, 'BUDGET_CAPACITY', '1')
    status, result = module.guard_launch(_form())
    assert status == 400 and result['error'] == 'budget-capacity'
    assert detail in result['detail']
    for name in ('submissions', 'work'):
        (tmp_path / name).mkdir()
    service = submissions.SubmissionService(
        platform_root=module.ROOT.parent, submissions_root=tmp_path / 'submissions',
        runs_root=tmp_path / 'runs', work_root=tmp_path / 'work', budget_dir=budget,
        budget_capacity=1, liveness=lambda: (True, {}))
    try:
        with pytest.raises(submissions.SubmissionRejected) as caught:
            service._guard()
        assert caught.value.problems[0]['code'] == 'service-not-ready'
        assert caught.value.problems[0]['detail'] == result['detail']
    finally:
        service.close()
    assert (budget / 'capacity').read_bytes() == stamp
    assert (budget / 'capacity.lock').read_bytes() == marker
