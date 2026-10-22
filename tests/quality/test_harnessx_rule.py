"""Rollback selection from recipe/gaia_evolver/run.py:1188-1257, :813-820.

Defaults follow upstream's 0.03 tolerance / three-pass noise guard. Tests of
the strict score-only policy explicitly select tolerance 0.0 / count guard 0.
These tests stage synthetic harness trees and stub the editor, never run a model,
dataset or driver, and inspect the actual emitted trajectory.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def hx(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "harnessx_rule", ROOT / "methods" / "harnessx" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("HG_HARNESSX_REGRESSION_TOLERANCE", "HARNESSX_TOLERANCE",
                 "HARNESSX_COST_WEIGHT", "HARNESSX_PASS_COUNT_NOISE_THRESHOLD",
                 "HARNESSX_COST_FIELD"):
        monkeypatch.delenv(name, raising=False)
    return mod


def _point(round_no, score, passed=None):
    point = {"round": round_no, "score": score, "measured_by_platform": True}
    if passed is not None:
        point["n_passed"] = passed
    return point


def _stage(tmp_path, history, *, snapshot=True, same_tree=False):
    base = tmp_path / "base"
    base.mkdir()
    (base / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (base / "agent.py").write_text("print('candidate')\n")
    (base / "candidate_only.txt").write_text("must disappear on restore\n")
    channel = base / "_harnessgrad"
    (channel / "history").mkdir(parents=True)
    for point in history:
        (channel / "history" / f"round-{point['round']}.json").write_text(
            json.dumps(point))
    (channel / "states").mkdir()
    state = channel / "states" / "round-1"
    index = {}
    if snapshot:
        state.mkdir()
        (state / "harness.json").write_bytes((base / "harness.json").read_bytes())
        (state / "agent.py").write_text("print('historical best')\n")
        (state / "assets").mkdir()
        (state / "assets" / "policy.txt").write_text("historical policy\n")
        index["round-1"] = {"harness_dir": str(state)}
        if same_tree:
            (base / "agent.py").write_bytes((state / "agent.py").read_bytes())
            (base / "candidate_only.txt").unlink()
            (base / "assets").mkdir()
            (base / "assets" / "policy.txt").write_bytes(
                (state / "assets" / "policy.txt").read_bytes())
    (channel / "states" / "index.json").write_text(json.dumps(index))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = {"platform_api_version": "0.1.0", "mode": "A",
               "base_harness": str(base), "workspace": str(workspace),
               "round_index": history[-1]["round"] + 1,
               "incumbent_score": history[-1]["score"],
               "trajectory_out": str(tmp_path / "trajectory.json")}
    return request, state


def _invoke(hx, request, monkeypatch, *, edit=False, forbid_ask=False,
            reply=None, raises=None):
    calls = []

    def fake_ask(*args, **kwargs):
        assert not forbid_ask, "a rollback must not spend a model call on another edit"
        calls.append(kwargs.get("base"))
        if raises is not None:
            raise raises
        if reply is not None:
            return reply
        if edit:
            return {"files": [{"path": "agent.py", "content": "print('next')\n"}],
                    "hypothesis": "synthetic editor proposal"}
        return {"no_change": "synthetic control"}

    monkeypatch.setattr(hx.editor, "ask", fake_ask)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    assert hx.main() == 0
    response = json.loads(stdout.getvalue())
    step = json.loads(Path(request["trajectory_out"]).read_text())["steps"][0]
    return response, step, calls


def test_regression_restores_historical_best_despite_a_one_pass_drop(
        hx, tmp_path, monkeypatch):
    # Upstream run.py:468 allows tolerance 0; :1227 allows count threshold 0.
    monkeypatch.setenv("HG_HARNESSX_REGRESSION_TOLERANCE", "0.0")
    monkeypatch.setenv("HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "0")
    # The latest candidate improves on R2, but still loses to historical best R1.
    request, state = _stage(tmp_path, [
        _point(0, .5, 4), _point(1, .875, 7),
        _point(2, .625, 5), _point(3, .75, 6)])
    response, step, calls = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert not calls
    assert response["changed"] is True and response["stop"] is False
    assert step["incumbent_round"] == 1
    dest = Path(step["harness_dir"])
    assert (dest / "agent.py").read_bytes() == (state / "agent.py").read_bytes()
    assert (dest / "assets" / "policy.txt").read_bytes() == (
        state / "assets" / "policy.txt").read_bytes()
    assert not (dest / "candidate_only.txt").exists()
    assert not (dest / "_harnessgrad").exists()
    gate = step["method_reported"]["harnessx_gate"]
    assert gate["decision"] == "REVERTED" and gate["graded_round"] == 3
    assert gate["best_round"] == 1 and gate["tunables"]["tolerance"] == 0.0
    assert "reverted: candidate 3" in gate["reason"]
    assert "best round 1" in gate["reason"]
    assert "-0.125" in gate["reason"] and "> tolerance 0.000" in gate["reason"]


def test_a_large_regression_declares_which_round_was_kept(hx, tmp_path, monkeypatch):
    request, _ = _stage(tmp_path, [
        _point(0, .5, 4), _point(1, .875, 7), _point(2, .375, 3)])
    _, step, _ = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert step["incumbent_round"] == 1
    assert step["method_reported"]["harnessx_revert_directive"]["state_available"] is True


def test_a_regression_without_pass_counts_is_still_reverted(hx, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "0")
    request, _ = _stage(tmp_path, [_point(0, .5), _point(1, .875), _point(2, .75)])
    _, step, _ = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert step["incumbent_round"] == 1
    gate = step["method_reported"]["harnessx_gate"]
    assert gate["decision"] == "REVERTED" and gate["count_delta"] is None


@pytest.mark.parametrize(("score", "tolerance"), [
    (.875, None), (1.0, None), (.75, ".125"), (.75, ".25")])
def test_equal_better_or_within_tolerance_does_not_revert(
        hx, tmp_path, monkeypatch, score, tolerance):
    if tolerance is not None:
        monkeypatch.setenv("HG_HARNESSX_REGRESSION_TOLERANCE", tolerance)
    request, _ = _stage(tmp_path, [_point(0, .5), _point(1, .875), _point(2, score)])
    _, step, calls = _invoke(hx, request, monkeypatch, edit=True)
    assert calls == [Path(request["base_harness"])]
    assert "incumbent_round" not in step
    assert step["method_reported"]["harnessx_gate"]["decision"] == "ACCEPTED"
    assert step["method_reported"]["harnessx_revert_directive"] is None
    assert Path(step["harness_dir"], "agent.py").read_text() == "print('next')\n"


def test_a_drop_beyond_the_configured_tolerance_reverts(hx, tmp_path, monkeypatch):
    monkeypatch.setenv("HG_HARNESSX_REGRESSION_TOLERANCE", ".125")
    monkeypatch.setenv("HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "0")
    request, _ = _stage(tmp_path, [_point(0, .5), _point(1, .875), _point(2, .625)])
    _, step, _ = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert step["incumbent_round"] == 1
    gate = step["method_reported"]["harnessx_gate"]
    assert gate["tunables"]["tolerance"] == .125
    assert "-0.250" in gate["reason"] and "> tolerance 0.125" in gate["reason"]


def test_missing_snapshot_records_selection_and_stops_without_editing(
        hx, tmp_path, monkeypatch):
    request, _ = _stage(tmp_path, [
        _point(0, .5, 4), _point(1, .875, 7), _point(2, .375, 3)], snapshot=False)
    response, step, _ = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert step["incumbent_round"] == 1
    assert response["changed"] is False and response["stop"] is True
    directive = step["method_reported"]["harnessx_revert_directive"]
    assert directive["state_available"] is False
    assert step["harness_dir"] == request["base_harness"]
    assert "not staged" in response["hypothesis"]


def test_restoring_an_identical_tree_keeps_selection_and_continues(
        hx, tmp_path, monkeypatch):
    request, _ = _stage(tmp_path, [
        _point(0, .5, 4), _point(1, .875, 7), _point(2, .375, 3)], same_tree=True)
    response, step, _ = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert step["incumbent_round"] == 1
    assert response["changed"] is False and response["stop"] is False
    assert step["method_reported"]["candidate_equals_incumbent"] is True


@pytest.mark.parametrize(("value", "expected"), [
    (None, .03), ("malformed", .03), ("nan", .03), ("inf", .03), ("-0.1", 0.0)])
def test_upstream_defaults_invalid_tolerance_fallback_and_negative_clamp(
        hx, monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("HG_HARNESSX_REGRESSION_TOLERANCE", value)
    tun = hx._tunables()
    # Upstream run.py:463-475, :1184. Negative tolerance remains clamped to 0.
    assert tun["tolerance"] == expected
    assert tun["pass_count_noise_threshold"] == 3


def test_the_new_tolerance_variable_takes_precedence(hx, monkeypatch):
    monkeypatch.setenv("HG_HARNESSX_REGRESSION_TOLERANCE", ".125")
    monkeypatch.setenv("HARNESSX_TOLERANCE", ".5")
    assert hx._tunables()["tolerance"] == .125


@pytest.mark.parametrize(("score", "measured"), [
    (None, True), (0.0, False), (float("nan"), True)])
def test_an_unmeasured_or_missing_score_is_not_a_zero_score_regression(
        hx, tmp_path, monkeypatch, score, measured):
    candidate = _point(2, score)
    candidate["measured_by_platform"] = measured
    request, _ = _stage(tmp_path, [_point(0, .5), _point(1, .875), candidate])
    _, step, calls = _invoke(hx, request, monkeypatch)
    gate = step["method_reported"]["harnessx_gate"]
    assert gate["decision"] == "NO_GATE"
    assert "no finite platform measurement" in gate["reason"]
    assert gate["best_round"] == 1 and gate["round_rate"] is None
    assert "incumbent_round" not in step
    assert len(calls) == 1


def test_an_unmeasured_high_score_cannot_become_historical_best(
        hx, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "0")
    unmeasured = _point(2, 1.0)
    unmeasured["measured_by_platform"] = False
    request, _ = _stage(tmp_path, [
        _point(0, .5), _point(1, .875), unmeasured, _point(3, .75)])
    _, step, _ = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert step["incumbent_round"] == 1
    assert step["method_reported"]["harnessx_gate"]["best_rate"] == .875


@pytest.mark.parametrize(("score", "passed", "decision"), [
    (.86, 172, "ACCEPTED"), (.845, 169, "ACCEPTED"), (.84, 168, "REVERTED")])
def test_default_tolerance_gates_against_best_without_drifting_it(
        hx, tmp_path, monkeypatch, score, passed, decision):
    # Counts differ by >=3, so the count guard cannot mask a tolerance error.
    request, _ = _stage(tmp_path, [
        _point(0, .5, 100), _point(1, .875, 175), _point(2, score, passed)])
    _, step, calls = _invoke(hx, request, monkeypatch, edit=True)
    gate = step["method_reported"]["harnessx_gate"]
    assert gate["tunables"]["tolerance"] == .03
    assert gate["tunables"]["pass_count_noise_threshold"] == 3
    assert gate["decision"] == decision and gate["noise_level"] is False
    assert gate["best_round"] == gate["updated_best_round"] == 1
    if decision == "REVERTED":
        assert not calls and step["incumbent_round"] == 1
    else:
        assert calls == [Path(request["base_harness"])]
        assert "incumbent_round" not in step


@pytest.mark.parametrize("snapshot", [True, False])
@pytest.mark.parametrize("declared", [True, False])
def test_rollback_keeps_the_graded_preregistration_or_reports_it_missing(
        hx, tmp_path, monkeypatch, snapshot, declared):
    # Upstream run.py:813-826 restores config, then back-fills the same journal.
    candidate = _point(2, .375, 3)
    previous = hx._preregistration({
        "hypothesis_id": "h_original", "levers": ["control"],
        "predicted_affected": ["synthetic_case"], "rollback_trigger": "score drop",
        "expected_global_gain": "fewer parse failures", "regression_risk": "latency",
        "cost_shift": "unchanged", "lever_argument": "parse guard, not instruction"})
    if declared:
        candidate["method_reported"] = {"harnessx_preregistration": previous}
    request, _ = _stage(tmp_path, [
        _point(0, .5, 4), _point(1, .875, 7), candidate], snapshot=snapshot)
    response, step, calls = _invoke(hx, request, monkeypatch, forbid_ask=True)
    assert not calls and step["incumbent_round"] == 1
    assert response["stop"] is (not snapshot)
    reported = step["method_reported"]
    assert reported["harnessx_gate"]["decision"] == "REVERTED"
    prereg = reported["harnessx_preregistration"]
    assert isinstance(prereg, dict)
    if declared:
        assert prereg == {**previous, "source": "previous_round_record"}
    else:
        assert prereg["source"] == "unavailable"
        assert prereg["hypothesis_id"] is None and prereg["levers"] == []
        assert "round 2" in prereg["reason"]


def test_provider_failure_after_a_tolerated_drop_records_the_actual_reason(
        hx, tmp_path, monkeypatch):
    request, _ = _stage(tmp_path, [
        _point(0, .5, 100), _point(1, .875, 175), _point(2, .86, 172)])
    response, step, calls = _invoke(
        hx, request, monkeypatch, raises=RuntimeError("503 no available channel"))
    assert calls == [Path(request["base_harness"])]
    reported = step["method_reported"]
    assert reported["error"] == "RuntimeError: 503 no available channel"
    assert reported["harnessx_gate"]["decision"] == "ACCEPTED"
    assert isinstance(reported["harnessx_preregistration"], dict)
    assert response["changed"] is False and response["stop"] is False
