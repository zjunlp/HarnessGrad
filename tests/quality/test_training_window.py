"""Training invocation admission, with a fake clock and no real waiting."""
from datetime import datetime, timedelta
import json
import pytest
from harnessgrad.training_window import decision, wait_for_admission


@pytest.mark.parametrize('timestamp,allowed', [
    ('2026-11-14T21:59:59+08:00', False), ('2026-11-14T22:00:00+08:00', True),
    ('2026-11-15T00:00:00+08:00', True), ('2026-11-15T06:49:00+08:00', True),
    ('2026-11-15T06:49:01+08:00', False), ('2026-11-15T08:00:00+08:00', False),
    ('2026-11-14T14:00:00+00:00', True)])
def test_complete_timeout_and_cleanup_fit_window(timestamp, allowed):
    assert decision(datetime.fromisoformat(timestamp), 4200)['allowed'] is allowed


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), 36000])
def test_impossible_or_invalid_timeout_fails_instead_of_waiting_forever(timeout):
    with pytest.raises(ValueError):
        decision(datetime.fromisoformat('2026-11-14T22:00:00+08:00'), timeout)


def test_waits_before_method_launch_and_retains_admission_receipt(tmp_path):
    timestamp = datetime.fromisoformat('2026-11-14T21:59:30+08:00')
    waits = []
    def now():
        return timestamp
    def sleep(seconds):
        nonlocal timestamp
        waits.append(seconds)
        timestamp += timedelta(seconds=seconds)
    receipt = tmp_path/'admission.json'
    result = wait_for_admission(4200, receipt, now=now, sleep=sleep)
    assert waits == [30]
    assert result['allowed'] and json.loads(receipt.read_text())['allowed']


def test_late_evening_invocation_that_cannot_fit_waits_until_next_night():
    status = decision(datetime.fromisoformat('2026-11-14T23:00:00+08:00'), 35000)
    assert not status['allowed']
    assert status['next_open'] == '2026-11-15T22:00:00+08:00'


def test_platform_waits_before_snapshot_and_subprocess_launch(tmp_path, monkeypatch):
    import harnessgrad.methods as methods
    import harnessgrad.training_window as window
    calls = []
    monkeypatch.setenv('HG_TRAIN_NIGHT_ONLY', '1')
    def admit(timeout, receipt):
        assert receipt == (tmp_path / 'run' / 'status.json').resolve()
        calls.append('admitted')
    monkeypatch.setattr(window, 'wait_for_admission', admit)
    monkeypatch.setattr(methods, 'snapshot', lambda *args: calls.append('snapshot') or {})
    monkeypatch.setattr(methods, 'diff', lambda *args: {'added': [], 'removed': [], 'modified': []})
    monkeypatch.setattr(methods, '_call_entrypoint', lambda *args, **kwargs: calls.append('launched') or {'ok': True})
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    assert methods._call_method(workspace, ['fake'], {}, 4200,
                                admission_receipt=tmp_path / 'run' / 'status.json') == {'ok': True}
    assert calls == ['admitted', 'snapshot', 'launched', 'snapshot']
    assert not (workspace / 'training-window-status.json').exists()


def test_night_admission_refuses_receipt_inside_harness(tmp_path, monkeypatch):
    import harnessgrad.methods as methods
    monkeypatch.setenv('HG_TRAIN_NIGHT_ONLY', '1')
    with pytest.raises(ValueError, match='separate admission receipt'):
        methods._call_method(tmp_path, ['fake'], {}, 4200)
    with pytest.raises(ValueError, match='inside the Harness workspace'):
        methods._call_method(tmp_path, ['fake'], {}, 4200,
                             admission_receipt=tmp_path / 'status.json')
    link = tmp_path.parent / (tmp_path.name + '-link')
    link.symlink_to(tmp_path / 'target')
    with pytest.raises(ValueError, match='cannot be a symlink'):
        methods._call_method(tmp_path, ['fake'], {}, 4200, admission_receipt=link)
