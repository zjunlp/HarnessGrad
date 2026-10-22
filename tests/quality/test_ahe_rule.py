"""AHE best-of-2: independent, distinct proposals before either is measured.

Upstream evolve.py:3715-3758 supplies variant strategy hints, :3857-3875 runs
them concurrently, :3313-3336 selects by pass rate / exceptions / index, and
:3185-3223 adopts only the winner and its manifest. Mode A delegates selection
to the platform; these controls exercise submission without a model or dataset.
"""
from __future__ import annotations

import copy
import io
import json
import sys
import threading
from pathlib import Path

import pytest

# The brief's focused entrypoint also retains all existing attribution, rollback,
# stopping and provider-error assertions without modifying their source file.
from test_ahe import *  # noqa: F401,F403
from test_ahe import _channel, _harmful_history, _load, _manifest, _point, _request


def _proposal(variant):
    return {
        "files": [
            {"path": "agent.py", "content": f"print('variant {variant}')\n"},
            {"path": f"variant_{variant}.py", "content": f"VARIANT = {variant}\n"},
        ],
        "hypothesis": f"repair mechanism {variant}",
        "changes": [{
            "id": f"chg-{variant}", "type": "improvement",
            "description": f"repair mechanism {variant}",
            "failure_pattern": f"failure-{variant}",
            "predicted_fixes": ["t1"], "risk_tasks": ["t2"],
            "constraint_level": "tool_impl",
        }],
    }


def _run_batch(tmp_path, monkeypatch, replies, *, history=None, barrier=None):
    mod = _load()
    history = history or [_point(0, {"t1": 0.0, "t2": 0.0})]
    base = _channel(tmp_path, history, states=[p["round"] for p in history])
    request = _request(base, tmp_path, round_index=history[-1]["round"] + 1)
    calls, batches = {}, []
    lock = threading.Lock()
    report_many = mod.editor.report_many

    def fake_ask(prompt, **kwargs):
        variant = 1 if "Best-of-2 variant 1" in prompt else 2
        with lock:
            calls[variant] = {"prompt": prompt, **kwargs}
        if barrier is not None:
            barrier.wait(timeout=5)
        reply = replies[variant - 1]
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)

    def capture_batch(req, **kwargs):
        batches.append(kwargs)
        return report_many(req, **kwargs)

    monkeypatch.setenv("HG_AHE_TARGET_PASS_RATE", "0.95")
    monkeypatch.setattr(mod.editor, "ask", fake_ask)
    monkeypatch.setattr(mod.editor, "report_many", capture_batch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert mod.main() == 0
    trajectory = json.loads(Path(request["trajectory_out"]).read_text())
    return trajectory, json.loads(output.getvalue()), calls, batches, base


def test_one_call_submits_two_distinct_variants(tmp_path, monkeypatch):
    trajectory, response, calls, batches, base = _run_batch(
        tmp_path, monkeypatch, [_proposal(1), _proposal(2)])
    steps = trajectory["steps"]
    assert len(steps) == 2, "best-of-2 must submit both candidates for measurement"
    assert len(batches) == 1, "submit the pair with editor.report_many"
    assert len(calls) == 2
    assert calls[1]["prompt"] != calls[2]["prompt"]
    assert "implementation" in calls[1]["prompt"]
    assert "different component" in calls[2]["prompt"]
    assert len({s["harness_dir"] for s in steps}) == 2
    for variant, step in enumerate(steps, 1):
        reported = step["method_reported"]
        assert reported["variant"] == variant
        assert reported["ahe_manifest"]["changes"][0]["id"] == f"chg-{variant}"
        assert "ahe_attribution" in reported
        assert reported["ahe_rollouts"]["submitted"] == 2
        assert reported["ahe_rollouts"]["winner_selection"] == "platform_train_score"
        candidate = Path(step["harness_dir"])
        assert (candidate / "agent.py").read_text() == f"print('variant {variant}')\n"
        assert (candidate / f"variant_{variant}.py").exists()
        assert not (candidate / f"variant_{3 - variant}.py").exists()
        assert calls[variant]["base"] == base
        assert calls[variant]["parent"] == base
    assert response["changed"] is True and response["stop"] is False
    assert (base / "agent.py").read_text() == "print('incumbent')\n"


def test_model_proposals_run_concurrently(tmp_path, monkeypatch):
    trajectory, _, calls, _, _ = _run_batch(
        tmp_path, monkeypatch, [_proposal(1), _proposal(2)],
        barrier=threading.Barrier(2))
    assert len(calls) == 2
    assert len(trajectory["steps"]) == 2


@pytest.mark.parametrize("duplicate", ["changes", "reordered_changes", "candidate"])
def test_duplicate_variants_fall_back_deterministically(tmp_path, monkeypatch, duplicate):
    first, second = _proposal(1), _proposal(2)
    if duplicate == "changes":
        second["changes"] = copy.deepcopy(first["changes"])
    elif duplicate == "reordered_changes":
        first["changes"] += copy.deepcopy(second["changes"])
        second["changes"] = list(reversed(copy.deepcopy(first["changes"])))
    else:
        second["files"] = copy.deepcopy(first["files"])
    trajectory, _, calls, batches, _ = _run_batch(
        tmp_path, monkeypatch, [first, second])
    assert len(calls) == 2
    assert not batches
    assert len(trajectory["steps"]) == 1
    reported = trajectory["steps"][0]["method_reported"]
    assert reported["variant"] == 1, "ties retain the first usable variant, without scores"
    reason = "identical_candidates" if duplicate == "candidate" else "identical_changes"
    assert reported["ahe_rollouts"]["fallback_reason"] == reason
    assert reported["ahe_rollouts"]["submitted"] == 1


@pytest.mark.parametrize("unusable", ["declined", "invalid", "provider_error"])
def test_one_bad_variant_preserves_the_usable_sibling(tmp_path, monkeypatch, unusable):
    bad = {
        "declined": {"no_change": "insufficient evidence"},
        "invalid": {"files": [{"path": "agent.py", "content": "def (\n"}]},
        "provider_error": RuntimeError("synthetic provider outage"),
    }[unusable]
    trajectory, response, calls, _, _ = _run_batch(
        tmp_path, monkeypatch, [bad, _proposal(2)])
    assert len(calls) == 2
    assert len(trajectory["steps"]) == 1
    reported = trajectory["steps"][0]["method_reported"]
    assert reported["variant"] == 2
    assert reported["ahe_rollouts"]["fallback_reason"] == "only_one_usable_variant"
    assert reported["ahe_rollouts"]["variant_outcomes"][0]["status"] == unusable
    assert response["changed"] is True and response["stop"] is False


def test_two_declines_are_one_no_change_with_a_reason(tmp_path, monkeypatch):
    trajectory, response, calls, batches, _ = _run_batch(
        tmp_path, monkeypatch, [{"no_change": "no evidence"}] * 2)
    assert len(calls) == 2 and not batches
    assert len(trajectory["steps"]) == 1
    assert response["changed"] is False and response["stop"] is False
    assert trajectory["steps"][0]["method_reported"]["ahe_rollouts"][
        "fallback_reason"] == "identical_changes"


def test_both_variants_use_the_rollback_state(tmp_path, monkeypatch):
    trajectory, _, calls, _, base = _run_batch(
        tmp_path, monkeypatch, [_proposal(1), _proposal(2)], history=_harmful_history())
    assert len(trajectory["steps"]) == 2
    for variant, step in enumerate(trajectory["steps"], 1):
        assert (Path(step["harness_dir"]) / "NOTES.md").read_text() == "notes from round 0\n"
        assert calls[variant]["parent"] == base / "_harnessgrad" / "states" / "round-0"
        assert step["method_reported"]["ahe_rule"]["edited_from_round"] == 0
        assert step["method_reported"]["ahe_attribution"]["summary"] == "chg-1: HARMFUL"


def test_sibling_attribution_compares_with_the_shared_parent():
    mod = _load()
    baseline = _point(0, {"t1": 0.0, "t2": 0.0})
    first = _point(1, {"t1": 1.0, "t2": 1.0},
                   manifest=_manifest(1, predicted=["t1"], risks=[]))
    second = _point(2, {"t1": 1.0, "t2": 0.0},
                    manifest=_manifest(1, predicted=["t1"], risks=[]))
    for point in (first, second):
        point["method_reported"]["ahe_rule"] = {"edited_from_round": 0}
    record = mod.attribution_history([baseline, first, second])[-1]
    assert record["diff"]["flipped"] == ["t1"]
    assert record["diff"]["regressed"] == []
    assert record["evidence"]["change_evaluations"][0]["verdict"] == "EFFECTIVE"


def test_siblings_do_not_count_as_two_failed_iterations():
    mod = _load()
    baseline = _point(0, {"t1": 0.0})
    siblings = [_point(r, {"t1": 0.0}, manifest=_manifest(
        1, predicted=["t1"], risks=[], level="prompt", pattern="same-failure"))
        for r in (1, 2)]
    records = mod.attribution_history([baseline, *siblings])
    assert mod.abandoned_levels(records) == {}
