"""Capped pilots select once before execution; no dataset payload is needed."""
import ast
from pathlib import Path

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


def test_driver_keeps_early_pilot_without_resampling():
    tree = ast.parse((Path(__file__).resolve().parents[2] / 'driver.py').read_text())
    matching = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                and ast.unparse(node.test) == "args.sampling == 'pilot_10pct'"]
    assert any(any(isinstance(n, ast.Assign) and ast.unparse(n) == 'side_ids = metrics.pilot_task_set(side_ids)'
                   for n in node.body) for node in matching)
    frozen = [node for node in ast.walk(tree) if isinstance(node, ast.If)
              and ast.unparse(node.test) == "args.sampling in ('diagnostic', 'pilot_10pct')"]
    assert any(any(isinstance(n, ast.Assign) and ast.unparse(n) == 'task_ids = list(side_ids)'
                   for n in node.body) for node in frozen)
