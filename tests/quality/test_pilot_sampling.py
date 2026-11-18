"""Capped pilots select once before execution; no dataset payload is needed."""
from types import SimpleNamespace

import pytest
from eval.run.metrics import diagnostic_task_set, pilot_task_set, select_task_set


@pytest.mark.parametrize('n,count', [(0,0),(1,1),(9,1),(10,1),(11,2),(118,12),
                                    (290,29),(291,30),(300,30),(301,30),
                                    (492,30),(500,30),(6913,30)])
def test_capped_pilot_size(n, count):
    assert len(pilot_task_set([f't/{i}' for i in range(n)])) == count


def test_selection_is_order_independent_and_unique():
    ids = [f't/{i}' for i in range(118)]
    assert pilot_task_set(ids) == pilot_task_set(list(reversed(ids)) + ids)


def test_explicit_all_is_preserved():
    assert select_task_set({'a': 0, 'b': 1}, 'all') == ['a', 'b']


def test_diagnostic_is_one_task_and_uses_no_scores():
    ids = [f't/{i}' for i in range(492)]
    assert diagnostic_task_set([]) == []
    assert diagnostic_task_set(ids) == pilot_task_set(ids)[:1]
    assert select_task_set(dict.fromkeys(ids, 0), 'diagnostic') == diagnostic_task_set(ids)
    assert select_task_set(dict.fromkeys(ids, 1), 'diagnostic') == diagnostic_task_set(ids)


def test_driver_keeps_early_pilot_without_resampling(monkeypatch):
    from harnessgrad.orchestration import h0, selected_dataset
    ids = [f't/{i}' for i in range(118)]
    tasks = [{'task_id': tid} for tid in ids]
    partition = selected_dataset.DatasetPartition(tasks, {}, {'train': ids, 'eval': []},
                                                  None, None, None, None)
    options = selected_dataset.SelectionOptions('demo', 'declared', 'train', None,
                                                'pilot_10pct', 'fixture', None, True, False)
    monkeypatch.setattr(selected_dataset.diagnostic_policy, 'require_review', lambda *a, **k: None)
    early = selected_dataset.select_run_tasks(options, partition, {'context': {}})
    assert early.ids == pilot_task_set(ids)
    measured = {'per_task': dict.fromkeys(early.ids, 0.0)}
    h0_options = h0.H0Options('demo', 'pilot_10pct', 'train', 1, 1, None, True)
    late = h0.select_after_h0(measured, early.tasks, early.ids, partition.split,
                              h0_options, SimpleNamespace(note=lambda *a, **k: None))
    assert late.task_ids == early.ids
