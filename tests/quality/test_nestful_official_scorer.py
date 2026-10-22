"""Pinned NESTFUL Win Rate: verbatim comparison + isolated gold counterexamples."""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

import data.datasets.nestful as dataset

SCORER = dataset.ROOT / "src/scorer.py"
AUDIT = Path(__file__).resolve().parents[2] / "data/datasets/nestful/audit.py"


def listit(t):
    return list(map(listit, t)) if isinstance(t, (list, tuple)) else t


# src/scorer.py:116-134, verbatim. calculate_ans is bound to an execution result in
# the differential comparison test, while audit.py executes the untouched actual loop.
def calculate_win_score(pred_func_calls, gold_ans, tools, executable_func_dir):
    if not pred_func_calls:
        return False
    
    gold_ans = json.loads(gold_ans)
    tools = json.loads(tools)
    pred_ans = calculate_ans(pred_func_calls, tools, executable_func_dir)
   
    if type(gold_ans) == float and type(pred_ans) == float:
        dec_no = len(str(gold_ans).split(".")[1])
        pred_ans = round(pred_ans, dec_no)
    if pred_ans == gold_ans:
        return True
    else:
        if type(pred_ans) == tuple and type(gold_ans) == list:
            pred_ans = listit(pred_ans)
            if pred_ans == gold_ans:
                return True
        return False


def test_verbatim_win_comparison_has_not_drifted():
    assert inspect.getsource(calculate_win_score) == "\n".join(SCORER.read_text().splitlines()[115:134]) + "\n"


@pytest.mark.parametrize("value,gold,expected", [
    (3, 3.87, False), (3.869, 3.87, True), (([1, 2], 3), [[1, 2], 3], True),
    ("0009", "0009", True), ("<bool @ 0x123>", "<bool @ 0x456>", False),
])
def test_official_comparison_differential(monkeypatch, value, gold, expected):
    import importlib.util
    spec = importlib.util.spec_from_file_location("nestful_audit_reference", AUDIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    upstream = module.official_namespace(dataset.ROOT)
    upstream["calculate_ans"] = lambda *args: value
    monkeypatch.setitem(globals(), "calculate_ans", lambda *args: value)
    args = ([{"label": "$var1"}], json.dumps(gold), "[]", "unused")
    assert calculate_win_score(*args) is expected
    assert upstream["calculate_win_score"](*args) is expected


def test_four_specific_gold_counterexamples_fail_official_execution():
    proc = subprocess.run([sys.executable, str(AUDIT), "--root", str(dataset.ROOT),
                           "--indices", "123,465,1531,1588"], capture_output=True,
                          text=True, timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["count"] == 4
    assert result["raised"] == []
    assert result["passed"] == 0
    assert [r["index"] for r in result["failed"]] == [123, 465, 1531, 1588]


def test_refusal_is_explicit_and_data_is_not_loaded_at_import():
    with pytest.raises(ValueError, match="unreproducible"):
        dataset.load()
    assert "Function/slot F1" in dataset.REFUSED
    assert "separately named" in dataset.REFUSED
    assert len(dataset._raw()) == 1861
