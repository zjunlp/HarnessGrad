"""GAIA publishes accuracy per difficulty level; the platform published one flat mean.

The level already crossed into the verifier (`data/datasets/gaia.py::_verify` puts it in the
gold record), and `data/datasets/gaia/verify/check.py` wrote only `reward 1.0`/`0.0`. So a
published GAIA mean could not be set beside the paper's Level 1/2/3 table, and the level
distribution of the *measured* set -- as opposed to the pool's -- was not recoverable from
anything the platform wrote.

This file pins the additive report that closes that gap, and -- the part that matters more --
pins that the headline number is the number it was before the report existed. A per-level
table that moved `score` would be a different measurement wearing the same name, so the
curve-point tests compare the point built with the report against the point built without it.

Everything here is synthetic and offline: the per-task records are dicts, and the check is run
against a two-line stub scorer so the test does not depend on the gated release being
provisioned. The real check's own contract is pinned in `test_gaia_official_scorer.py`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.metrics import mean  # noqa: E402
from harnessgrad.records import (  # noqa: E402
    _curve_point,
    level_fields,
    per_level_breakdown,
)

CHECK = ROOT / "data" / "datasets" / "gaia" / "verify" / "check.py"


#: `(task_id, score, level)`. 12 measured tasks: Level 1 is 4/5, Level 2 is 2/4, Level 3 is
#: 0/2, and one task carries no level at all. The mean (7/12) is deliberately not exactly
#: representable in binary, so "unchanged" is asserted on the float, not on a rounded print.
TASKS = (
    ("gaia/0", 1.0, "1"), ("gaia/1", 1.0, "1"), ("gaia/2", 1.0, "1"),
    ("gaia/3", 1.0, "1"), ("gaia/4", 0.0, "1"),
    ("gaia/5", 1.0, "2"), ("gaia/6", 1.0, "2"), ("gaia/7", 0.0, "2"),
    ("gaia/8", 0.0, "2"),
    ("gaia/9", 0.0, "3"), ("gaia/10", 0.0, "3"),
    ("gaia/11", 1.0, None),
)


def _records(spec=TASKS) -> dict[str, dict]:
    return {tid: {"score": score, "level": level} for tid, score, level in spec}


def _result(spec=TASKS) -> dict:
    """A runner-shaped result: `per_task` is what was measured, `verdicts` carry the check's
    evidence. That is exactly the pair `_curve_point` reads at one of its real call sites."""
    return {
        "per_task": {tid: score for tid, score, _ in spec},
        "verdicts": {tid: {"kind": "command", "evidence": {"measured": True, "level": level}}
                     for tid, _, level in spec},
    }


def _point(res: dict, **level_kwargs) -> dict:
    ids = list(res["per_task"])
    scores = [res["per_task"][tid] for tid in ids]
    return _curve_point(
        run_id="synthetic-gaia", round_index=0, identity={"harness_sha": "abc123"},
        sampling={"dataset": "gaia", "task_ids": ids}, scores=scores, task_ids=ids,
        cost={}, cumulative={}, side="eval", **level_kwargs)


# --------------------------------------------------------------- the breakdown itself ---


def test_each_level_reports_measured_tasks_ones_and_accuracy():
    breakdown = per_level_breakdown(_records())
    assert breakdown["levels"]["1"] == {"measured": 5, "scored_1": 4, "accuracy": 0.8}
    assert breakdown["levels"]["2"] == {"measured": 4, "scored_1": 2, "accuracy": 0.5}
    assert breakdown["levels"]["3"] == {"measured": 2, "scored_1": 0, "accuracy": 0.0}
    assert breakdown["unknown"] == {"measured": 1, "scored_1": 1, "accuracy": 1.0}
    assert breakdown["measured_tasks"] == 12
    assert breakdown["level_tasks"] == 11


def test_a_level_nothing_measured_is_a_row_of_zeros_not_a_missing_row():
    """GAIA's table has three rows. A run that measured only Level 1 tasks must still say so
    about Levels 2 and 3 -- an absent row cannot be told from a level that does not exist."""
    breakdown = per_level_breakdown(_records([("gaia/0", 1.0, "1")]))
    assert breakdown["levels"]["1"] == {"measured": 1, "scored_1": 1, "accuracy": 1.0}
    assert breakdown["levels"]["2"] == {"measured": 0, "scored_1": 0, "accuracy": None}
    assert breakdown["levels"]["3"] == {"measured": 0, "scored_1": 0, "accuracy": None}
    assert breakdown["unknown"]["measured"] == 0


def test_an_unexpected_level_and_an_unlabelled_task_are_both_reportable():
    """A fourth label is reported rather than dropped (a future release may add one), an int
    label normalises to the pool's string form, and every way of saying "no label" is
    `unknown` rather than a silent zero in Level 1."""
    breakdown = per_level_breakdown({
        "a": {"score": 1.0, "level": "4"},
        "b": {"score": 1.0, "level": 2},
        "c": {"score": 0.0, "level": None},
        "d": {"score": 0.0},                 # key absent
        "e": {"score": 0.0, "level": ""},    # empty label
        "f": {"score": 0.0, "level": True},  # a bool is not a level
        "g": {"score": 0.0, "level": float("nan")},   # nor is a number that is not one
    })
    assert breakdown["levels"]["4"] == {"measured": 1, "scored_1": 1, "accuracy": 1.0}
    assert breakdown["levels"]["2"]["measured"] == 1
    assert breakdown["unknown"] == {"measured": 5, "scored_1": 0, "accuracy": 0.0}
    assert breakdown["levels_reported"] == ["2", "4"]


# ------------------------------------------------------- where the report is assembled ---


def test_the_check_evidence_is_where_the_level_comes_from():
    """`level_fields` reads the label off the check's own evidence, so the dataset that owns
    the label is the only thing that has to know what a level is."""
    fields = level_fields(_result(), list(_result()["per_task"]))
    assert fields["per_level"]["levels"]["1"]["scored_1"] == 4
    assert fields["per_level"]["unknown"]["measured"] == 1


def test_tasks_the_platform_did_not_measure_are_not_in_any_level():
    """`invalid`/unmeasured tasks are absent from `per_task` (INTERFACE.md §2.5.7), so a
    per-level count is a count of **measured** tasks and can never sum past `n_scored`."""
    res = _result()
    res["invalid"] = {"gaia/3": {"stage": "check", "detail": "synthetic"}}
    del res["per_task"]["gaia/3"]
    fields = level_fields(res, list(res["per_task"]))
    assert fields["per_level"]["levels"]["1"]["measured"] == 4
    assert fields["per_level"]["measured_tasks"] == 11


def test_a_measured_task_the_check_labelled_nothing_lands_in_unknown():
    res = {"per_task": {"gaia/0": 1.0, "gaia/1": 0.0, "gaia/2": 1.0},
           "verdicts": {"gaia/0": {"evidence": {"level": "1"}},
                        "gaia/1": {"evidence": {"measured": True}},
                        # a `files` check writes no evidence at all
                        "gaia/2": {}}}
    fields = level_fields(res, list(res["per_task"]))
    assert fields["per_level"]["levels"]["1"]["measured"] == 1
    assert fields["per_level"]["unknown"]["measured"] == 2


def test_no_reported_level_is_null_not_an_invented_all_unknown_table():
    """A dataset that publishes no per-task label must not be summarised as "every task's
    level is unknown": the platform looked and there was nothing to look at."""
    res = {"per_task": {"t": 1.0}, "verdicts": {"t": {"evidence": {"measured": True}}}}
    assert level_fields(res, ["t"]) == {"per_level": None}
    point = _point(res, **level_fields(res, ["t"]))
    assert "per_level" in point and point["per_level"] is None


# ------------------------------------------- the headline number must be the same one ---


def test_the_level_report_does_not_change_the_headline_mean_or_any_other_number():
    """The before/after proof: the point built **without** the report (the pre-change call)
    and the point built **with** it differ in exactly one key, and every number -- `score`,
    `score_ci95`, `per_task`, `n_scored`, `n_passed` -- is identical, bit for bit."""
    res = _result()
    ids = list(res["per_task"])
    scores = [res["per_task"][tid] for tid in ids]

    before = _point(res)
    after = _point(res, **level_fields(res, ids))

    assert before["score"] == after["score"]
    assert repr(before["score"]) == repr(after["score"])
    assert before["per_task"] == after["per_task"] == res["per_task"]
    assert (before["n_scored"], before["n_passed"]) == (after["n_scored"],
                                                        after["n_passed"]) == (12, 7)
    # The report is not a second score: the platform's mean is still the declared
    # `arithmetic_mean` over measured tasks, computed from the same list.
    assert after["score"] == sum(scores) / len(scores)
    assert after["score"] == mean(scores, dataset="gaia")
    # And the only difference on the point is the additive report.
    ignore = {"per_level", "timestamp_utc"}
    assert ({k: v for k, v in after.items() if k not in ignore}
            == {k: v for k, v in before.items() if k not in ignore})
    assert before.get("per_level") is None and after["per_level"]


def test_the_report_partitions_the_same_measured_set_as_score():
    """Checks that can be done on a published point alone: the levels plus `unknown` cover
    every scored task exactly once, and the `scored_1` counts sum to `n_passed`."""
    point = _point(_result(), **level_fields(_result(), list(_result()["per_task"])))
    breakdown = point["per_level"]
    assert (sum(row["measured"] for row in breakdown["levels"].values())
            + breakdown["unknown"]["measured"]) == point["n_scored"]
    assert (sum(row["scored_1"] for row in breakdown["levels"].values())
            + breakdown["unknown"]["scored_1"]) == point["n_passed"]
    assert breakdown["measured_tasks"] == point["n_scored"]


# ------------------------------------------------------ the check that emits the label ---


def _run_check(work: Path, answer: str | None, level: object) -> tuple[int, str | None, dict]:
    """The real `check.py`, against a stub scorer: this test is about the evidence contract,
    not about the release, so it must run on a machine where the pool is not provisioned."""
    (work / "scorer.py").write_text(
        "def question_scorer(answer, gold):\n"
        "    return answer.strip().lower() == gold.strip().lower()\n", encoding="utf-8")
    (work / "check.py").write_text(CHECK.read_text(encoding="utf-8"), encoding="utf-8")
    (work / "answer.txt").write_text("" if answer is None else answer, encoding="utf-8")
    (work / "gold.json").write_text(json.dumps(
        {"task_id": "gaia/0", "gold": "41", "level": level}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "check.py", "--answer", "answer.txt", "--gold", "gold.json",
         "--reward-file", "verifier/reward.txt", "--evidence-file", "verifier/evidence.json"],
        cwd=work, capture_output=True, text=True, timeout=120)
    reward_file = work / "verifier" / "reward.txt"
    reward = reward_file.read_text().strip() if reward_file.is_file() else None
    evidence = json.loads((work / "verifier" / "evidence.json").read_text())
    return proc.returncode, reward, evidence


def test_the_check_reports_the_level_without_letting_it_near_the_reward(tmp_path):
    """Both outcomes carry the level, and the reward is the same 1/0 it was before. A
    missing answer -- a measured zero -- carries it too."""
    for answer, expected_reward in (("41", "1"), ("somebody else", "0")):
        work = tmp_path / expected_reward
        work.mkdir()
        code, reward, evidence = _run_check(work, answer, "3")
        assert (code, reward) == (0, expected_reward), evidence
        assert evidence["measured"] is True and evidence["level"] == "3"

    work = tmp_path / "missing"
    work.mkdir()
    code, reward, evidence = _run_check(work, None, "2")
    assert (code, reward, evidence["measured"]) == (0, "0", True)
    assert evidence["level"] == "2"


def test_the_progress_record_the_fold_reads_carries_the_level(tmp_path):
    """The per-task record a fold reads (`progress/round-N/<task>.json`) is the join key:
    without it the fold would have to open `verdicts/` as well."""
    from eval.observe.tracing import _write_progress

    measured = [{"verdict": {"score": 1.0, "passed": True, "evidence": {"level": "2"}},
                 "exit_code": 0, "stderr": "",
                 "trace": '{"wall_clock_s": {"total": 3.0}}'}]
    _write_progress(tmp_path, 0, "gaia/7", measured, None)
    record = json.loads((tmp_path / "progress" / "round-0" / "gaia" / "7.json")
                        .read_text(encoding="utf-8"))
    assert record["task_id"] == "gaia/7" and record["score"] == 1.0
    assert record["level"] == "2"

    # A check that reports no level writes no `level` key -- not a null in Level 1.
    _write_progress(tmp_path, 0, "gaia/8",
                    [{"verdict": {"score": 0.0, "passed": False}, "exit_code": 0,
                      "stderr": "", "trace": ""}], None)
    plain = json.loads((tmp_path / "progress" / "round-0" / "gaia" / "8.json").read_text())
    assert "level" not in plain
