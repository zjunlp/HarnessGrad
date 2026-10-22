"""RRSI retention receipts: rrsi/selection.py:97-137, rrsi/loop.py:327-340.

Only synthetic curve points and local harness files; every model call is stubbed.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def rrsi(monkeypatch):
    for name in list(os.environ):
        if name.startswith("HG_RRSI_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HG_RRSI_DELTA", "0.05")
    spec = importlib.util.spec_from_file_location(
        "rrsi_retention_rule", ROOT / "methods/rrsi/run.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _point(round_no, score, tokens=100):
    return {"round": round_no, "train_score": score, "score": 1 - score,
            "cost": {"harness_tokens": tokens,
                     "harness_tokens_reported_by_tasks": int(tokens is not None)}}


def _invoke(rrsi, history, tmp_path, monkeypatch, outcome="no_change"):
    base = tmp_path / "base"
    base.mkdir()
    (base / "harness.json").write_text(json.dumps(
        {"name": "synthetic", "version": "1.0", "entrypoint": "agent.py"}))
    (base / "agent.py").write_text("print('base')\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trajectory = tmp_path / "trajectory.json"
    request = {"platform_api_version": "0.1.0", "base_harness": str(base),
               "workspace": str(workspace), "trajectory_out": str(trajectory),
               "round_index": len(history)}
    monkeypatch.setattr(rrsi.editor, "read_request", lambda: request)
    monkeypatch.setattr(rrsi.editor, "load_history", lambda _: history)

    def propose(*args, **kwargs):
        if outcome == "provider_failure":
            raise RuntimeError("synthetic provider failure")
        if outcome == "apply_failure":
            return {}, workspace / "candidate", "synthetic apply failure"
        if outcome == "no_change":
            reply = {"no_change": "synthetic no change"}
        else:
            reply = {"files": ([] if outcome == "unusable" else [
                {"path": "agent.py", "content": "print('candidate')\n",
                 "component": "prompt"}]), "hypothesis": "synthetic proposal"}
        return reply, workspace / "candidate", ""

    monkeypatch.setattr(rrsi.editor, "propose_and_apply", propose)
    monkeypatch.setenv("HG_RRSI_REPAIR_ROUNDS", "1" if outcome == "repair" else "0")
    monkeypatch.setattr(rrsi, "_gate", lambda *a, **k: (
        (["synthetic rejection"], {"verdict": "reject"})
        if outcome in {"drop", "repair"} else ([], {"verdict": "accept"})))

    def ask(*args, **kwargs):
        assert outcome == "repair", "unexpected model call"
        return {"no_change": "synthetic repair declined"}

    monkeypatch.setattr(rrsi.editor, "ask", ask)
    reports = []
    for name in ("report", "report_many"):
        original = getattr(rrsi.editor, name)

        def capture(*args, _original=original, **kwargs):
            reports.extend(kwargs.get("steps", [kwargs]))
            return _original(*args, **kwargs)

        monkeypatch.setattr(rrsi.editor, name, capture)
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    assert rrsi.main() == 0
    return json.loads(trajectory.read_text())["steps"][0], reports, json.loads(stdout.getvalue())


@pytest.mark.parametrize("outcome", ["no_change", "candidate", "unusable", "drop",
                                     "repair", "apply_failure", "provider_failure"])
def test_an_earlier_retained_round_reaches_the_report(rrsi, tmp_path, monkeypatch, outcome):
    """Below S* - delta => keep H_t, not the rejected H' (loop.py:338-340)."""
    history = [_point(0, 0.4), _point(1, 0.8), _point(2, 0.3)]
    step, reports, response = _invoke(rrsi, history, tmp_path, monkeypatch, outcome)
    assert len(reports) == 1
    assert reports[0].get("incumbent_round") == 1
    assert step.get("incumbent_round") == 1
    rule = step["method_reported"]["rrsi_rule"]
    assert rule["incumbent_adjudication"]["admissible"] is False
    assert rule["retention"]["incumbent_round"] == 1
    assert rule["retention"]["reason"]
    if outcome != "candidate":
        assert response["changed"] is False
        assert response["stop"] is False


@pytest.mark.parametrize("score,tokens,expected", [
    (0.61, 200, 1),  # Inside the band, but the cost rule fails.
    (0.58, 50, 2),   # Lower score inside the band; saved tokens make it admissible.
    (0.70, 110, 2),  # Gain above the band and within its token budget.
])
def test_retention_uses_both_cost_branches(rrsi, tmp_path, monkeypatch, score, tokens, expected):
    """selection.py:81-94,124-137: no extra 'best score ever' selection rule."""
    step, reports, _ = _invoke(
        rrsi, [_point(0, 0.4), _point(1, 0.6), _point(2, score, tokens)],
        tmp_path, monkeypatch)
    assert reports[0].get("incumbent_round") == expected
    assert step.get("incumbent_round") == expected
    verdict = step["method_reported"]["rrsi_rule"]["incumbent_adjudication"]
    assert verdict["cost_rule_active"] is True
    assert verdict["admissible"] is (expected == 2)


@pytest.mark.parametrize("history,reason", [
    ([], "baseline"),
    ([_point(0, 0.4)], "baseline"),
    ([_point(0, 0.4), _point(1, 0.6, None)], "token"),
    ([{"round": 0, "score": 0.4}, {"round": 1, "score": 0.9}], "train"),
])
def test_no_retention_claim_without_a_verdict(rrsi, tmp_path, monkeypatch, history, reason):
    """An unevaluated port rule is not a selection.py:119 admissibility verdict."""
    step, reports, _ = _invoke(rrsi, history, tmp_path, monkeypatch)
    assert "incumbent_round" not in reports[0]
    assert "incumbent_round" not in step
    retention = step["method_reported"]["rrsi_rule"]["retention"]
    assert retention["incumbent_round"] is None
    assert reason in retention["reason"]


def test_training_only_points_use_the_studied_score(rrsi, tmp_path, monkeypatch):
    history = [_point(0, 0.4), _point(1, 0.8), _point(2, 0.3)]
    for point in history:
        point["score_kind"] = "training"
        point["score"] = point.pop("train_score")
    step, _, _ = _invoke(rrsi, history, tmp_path, monkeypatch)
    assert step.get("incumbent_round") == 1


def test_deviations_describe_the_remaining_runtime_gap():
    meta = json.loads((ROOT / "methods/rrsi/method.json").read_text())
    assert all(item["direction"] != "unknown" for item in meta["deviations"])
    pool = meta["deviations"][0]
    assert pool["direction"] == "weaker"
    for capability in ("report_many", "kept_round", "states/", "rrsi/loop.py:290-307"):
        assert capability in pool["why"]
