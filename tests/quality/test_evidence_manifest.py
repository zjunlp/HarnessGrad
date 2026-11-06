"""Tests for manifest self-consistency, not external receipt verification."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harnessgrad import evidence


def pack(**overrides):
    """A minimal pack that is valid, so each test can break exactly one thing."""
    document = {
        "schema": evidence.SCHEMA,
        "created_utc": "2026-11-06T00:00:00Z",
        "submission": {"run_id": "sub", "round": 3},
        "requests": [
            {
                "id": "R1.2",
                "concern": "the comparison to the baseline is unclear",
                "status": "measured",
                "runs": ["sub", "base"],
                "result": {"run_id": "sub", "round": 3, "metric": "score", "value": 0.42},
                "vs_baseline": {
                    "baseline_run": "base",
                    "diff": {"changed": True, "config": ["sampling.trials"]},
                },
            }
        ],
        "attempts": [
            {"run_id": "base", "purpose": "baseline", "status": "measured", "rounds": [0]},
            {"run_id": "sub", "purpose": "follow-up", "status": "measured", "rounds": [0, 3]},
        ],
        "selection": {
            "reported": {"run_id": "sub", "round": 3},
            "rule": "the last round of the run named in the submission",
        },
        "budget": {"rounds": 3, "trials": 3, "source": "cost fields recorded per curve point"},
    }
    document.update(overrides)
    return document


def _problems(document):
    problems, _ = evidence.check_manifest(document)
    return problems


def test_a_pack_with_receipts_passes():
    problems, notes = evidence.check_manifest(pack())
    assert problems == []
    assert notes == []


def test_schema_is_pinned():
    problems = _problems(pack(schema="harnessgrad.evidence.v0"))
    assert any("expected 'harnessgrad.evidence.v1'" in p for p in problems)


def test_a_missing_top_level_field_is_a_refusal():
    document = pack()
    del document["selection"]
    assert any("selection" in p for p in _problems(document))


def test_attempt_ledger_cannot_reuse_a_run_id():
    document = pack()
    document["attempts"].append({"run_id": "sub", "purpose": "second", "status": "measured",
                                 "rounds": [1]})
    assert any("share a run_id" in p for p in _problems(document))


def test_a_failed_attempt_must_say_why():
    document = pack()
    document["attempts"].append({"run_id": "boom", "purpose": "wider search", "status": "failed"})
    assert any("boom" in p and "reason" in p for p in _problems(document))


def test_a_measured_attempt_must_name_its_rounds():
    document = pack()
    document["attempts"][0].pop("rounds")
    assert any("name the rounds" in p for p in _problems(document))


def test_a_request_cannot_cite_a_run_that_is_not_in_the_ledger():
    document = pack()
    document["requests"][0]["runs"].append("ghost")
    assert any("ghost" in p for p in _problems(document))


def test_a_failed_attempt_is_never_a_result():
    document = pack()
    document["attempts"].append({"run_id": "boom", "purpose": "wider search", "status": "failed",
                                 "reason": "provider quota"})
    document["requests"][0]["result"]["run_id"] = "boom"
    document["requests"][0]["runs"].append("boom")
    assert any("not a result" in p for p in _problems(document))


def test_a_result_must_name_a_round_that_was_measured():
    document = pack()
    document["requests"][0]["result"]["round"] = 9
    assert any("was not measured" in p for p in _problems(document))


def test_an_unmeasured_request_must_say_why():
    document = pack()
    document["requests"][0].update(status="unmeasured", runs=[], result=None, reason=None)
    assert any("unmeasured" in p and "why" in p for p in _problems(document))


def test_an_unmeasured_request_cannot_carry_a_result():
    document = pack()
    document["requests"][0]["status"] = "unmeasured"
    document["requests"][0]["reason"] = "not run"
    assert any("cannot carry a result" in p for p in _problems(document))


def test_selection_must_state_its_rule():
    document = pack()
    document["selection"].pop("rule")
    assert any("cherry-pick" in p for p in _problems(document))


def test_selection_cannot_report_a_stopped_attempt():
    document = pack()
    document["attempts"].append({"run_id": "half", "purpose": "longer run", "status": "stopped",
                                 "reason": "deadline", "rounds": [0, 1]})
    document["selection"]["reported"] = {"run_id": "half", "round": 1}
    assert any("stopped attempt" in p for p in _problems(document))


def test_a_rejected_run_has_to_explain_itself():
    document = pack()
    document["selection"]["rejected"] = [{"run_id": "base"}]
    assert any("without saying why" in p for p in _problems(document))


def test_a_baseline_that_was_not_measured_cannot_anchor_a_comparison():
    document = pack()
    document["attempts"].append({"run_id": "probe", "purpose": "smoke", "status": "invalid",
                                 "reason": "scorer refused", "rounds": [0]})
    document["requests"][0]["vs_baseline"]["baseline_run"] = "probe"
    assert any("nothing to stand on" in p for p in _problems(document))


def test_budget_must_name_its_source():
    document = pack()
    document["budget"].pop("source")
    assert any("source" in p for p in _problems(document))


def test_an_empty_baseline_diff_is_a_note_not_a_refusal():
    document = pack()
    document["requests"][0]["vs_baseline"]["diff"] = {"changed": False}
    problems, notes = evidence.check_manifest(document)
    assert problems == []
    assert any("baseline diff is empty" in n for n in notes)


def test_summary_counts_the_attempts_a_reviewer_looks_for():
    document = pack()
    document["attempts"] += [
        {"run_id": "boom", "purpose": "wider search", "status": "failed", "reason": "quota"},
        {"run_id": "half", "purpose": "longer run", "status": "stopped", "reason": "deadline",
         "rounds": [0]},
    ]
    counts = evidence.summary(document)
    assert counts["attempts"] == 4
    assert counts["by_status"]["measured"] == 2
    assert counts["by_status"]["failed"] == 1
    assert counts["by_status"]["stopped"] == 1
    assert counts["requests"] == 1 and counts["answered"] == 1


def test_referenced_runs_covers_result_baseline_and_rejections():
    document = pack()
    document["selection"]["rejected"] = [{"run_id": "half", "why": "stopped early"}]
    document["attempts"].append({"run_id": "half", "purpose": "longer run", "status": "stopped",
                                 "reason": "deadline", "rounds": [0]})
    assert evidence.referenced_runs(document) == {"base", "sub", "half"}


def test_round_trip_is_deterministic(tmp_path: Path):
    path = tmp_path / "evidence.json"
    evidence.write(pack(), path)
    first = path.read_text(encoding="utf-8")
    evidence.write(evidence.load(path), path)
    assert path.read_text(encoding="utf-8") == first
    assert json.loads(first)["schema"] == evidence.SCHEMA


@pytest.mark.parametrize("field,value", [
    ("submission", "bad"), ("attempts", {}), ("requests", "bad"),
    ("selection", []), ("budget", 1),
])
def test_invalid_top_level_shapes_are_problems(field, value):
    assert _problems(pack(**{field: value}))


@pytest.mark.parametrize("mutate", [
    lambda p: p["attempts"][0].update(run_id=[]),
    lambda p: p["attempts"][0].update(rounds=[True]),
    lambda p: p["requests"][0].update(id=[]),
    lambda p: p["requests"][0].update(runs=[{}]),
    lambda p: p["requests"][0].update(result="bad"),
    lambda p: p["requests"][0].update(vs_baseline={"baseline_run": []}),
    lambda p: p["selection"].update(rejected=["bad"]),
])
def test_invalid_nested_shapes_are_problems(mutate):
    document = pack()
    mutate(document)
    assert _problems(document)


@pytest.mark.parametrize("submission", [{}, {"run_id": "sub", "round": "3"},
                                         {"run_id": "sub", "round": True}])
def test_submission_requires_valid_state(submission):
    assert _problems(pack(submission=submission))


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), "1"])
def test_result_requires_finite_numeric_value(value):
    document = pack()
    document["requests"][0]["result"]["value"] = value
    assert _problems(document)


def test_result_requires_metric():
    document = pack()
    del document["requests"][0]["result"]["metric"]
    assert _problems(document)


def test_selection_must_match_submission_and_rejections_must_be_in_ledger():
    document = pack()
    document["selection"]["reported"] = {"run_id": "base", "round": 0}
    document["selection"]["rejected"] = [{"run_id": "ghost", "why": "failed"}]
    problems = _problems(document)
    assert any("does not match submission" in p for p in problems)
    assert any("ghost" in p and "ledger" in p for p in problems)


def test_unknown_request_status_is_rejected():
    document = pack()
    document["requests"][0]["status"] = "failed"
    assert _problems(document)


def test_stopped_attempt_can_record_reached_rounds():
    document = pack()
    document["attempts"].append({"run_id": "half", "purpose": "test", "status": "stopped",
                                 "reason": "deadline", "rounds": [0, 1]})
    assert not _problems(document)


def test_manifest_consistency_does_not_verify_receipts():
    document = pack()
    document["requests"][0]["result"]["value"] = 999
    assert not _problems(document)


def test_load_rejects_malformed_and_missing_manifest(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(evidence.EvidenceError):
        evidence.load(path)
    with pytest.raises(evidence.EvidenceError):
        evidence.load(tmp_path / "absent.json")
