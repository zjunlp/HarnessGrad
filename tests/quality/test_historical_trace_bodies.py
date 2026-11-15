"""Shared H0 and resume must restore complete namespaced task evidence offline."""
import stat

import pytest

from harnessgrad.orchestration.h0 import H0Donor, materialize_donor_traces
from harnessgrad.orchestration.historical_evidence import read_trace_bodies


def _write(directory, name, body):
    path = directory / (name + '.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


@pytest.mark.parametrize('prefix', ['', 'task-family/'])
def test_restores_complete_task_ids_and_ordered_trial_bodies(tmp_path, prefix):
    task = prefix + '41'
    other = 'other-family/41'
    _write(tmp_path, task, 'original final trace\n')
    _write(tmp_path, other, 'distinct task with the same basename\n')
    _write(tmp_path, task + '.trial2', 'second trial\n')
    _write(tmp_path, task + '.trial1', 'first trial\n')
    traces, trials = read_trace_bodies(tmp_path)
    assert traces == {task: 'original final trace\n',
                      other: 'distinct task with the same basename\n'}
    assert trials == {task: ['first trial\n', 'second trial\n']}


def test_trial_marker_in_task_namespace_does_not_consume_the_trial_suffix(tmp_path):
    _write(tmp_path, 'family.trial-set/41', 'original trace\n')
    _write(tmp_path, 'family.trial-set/41.trial1', 'original trial\n')
    assert read_trace_bodies(tmp_path) == (
        {'family.trial-set/41': 'original trace\n'},
        {'family.trial-set/41': ['original trial\n']})


def test_shared_h0_populates_in_memory_evidence_from_nested_donor(tmp_path):
    donor = tmp_path / 'donor'
    _write(donor / 'round0_traces', 'task-a/41', 'trace A\n')
    _write(donor / 'round0_traces', 'task-b/41', 'trace B\n')
    _write(donor / 'round0_traces', 'task-a/41.trial1', 'trial A\n')
    recipient = tmp_path / 'recipient'
    recipient.mkdir()
    base = {'per_task': {'task-a/41': 0.25, 'task-b/41': 0.5}}
    materialize_donor_traces(H0Donor('donor', donor, None, {}), recipient, base)
    assert base['traces'] == {'task-a/41': 'trace A\n', 'task-b/41': 'trace B\n'}
    assert base['trial_traces'] == {'task-a/41': ['trial A\n']}
    assert base['per_task'] == {'task-a/41': 0.25, 'task-b/41': 0.5}
    assert (recipient / 'round0_traces/task-a/41.jsonl').read_bytes() == (
        donor / 'round0_traces/task-a/41.jsonl').read_bytes()


def test_read_only_fixed_donor_keeps_its_modes_and_recipient_can_persist(tmp_path):
    donor = tmp_path / 'donor'
    _write(donor / 'round0_traces', 'task-a/41', 'fixed original\n')
    trace = donor / 'round0_traces/task-a/41.jsonl'
    trace.chmod(0o400)
    trace.parent.chmod(0o500)
    trace.parent.parent.chmod(0o500)
    recipient = tmp_path / 'recipient'
    recipient.mkdir()
    try:
        base = {}
        materialize_donor_traces(H0Donor('fixed', donor, None, {}), recipient, base)
        copy = recipient / 'round0_traces/task-a/41.jsonl'
        assert base['traces'] == {'task-a/41': 'fixed original\n'}
        copy.write_text(base['traces']['task-a/41'])
        (copy.parent / '41.trial1.jsonl').write_text('recipient trial\n')
        assert trace.read_text() == 'fixed original\n'
        assert stat.S_IMODE(trace.stat().st_mode) == 0o400
        assert stat.S_IMODE(trace.parent.stat().st_mode) == 0o500
    finally:
        trace.parent.parent.chmod(0o700)
        trace.parent.chmod(0o700)
        trace.chmod(0o600)
