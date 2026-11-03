"""`tools/run_summary.py`: one page per run, and no fabricated zeros.

The fixtures are hand-written run directories, not copies of the archive: the point is to
pin the shape of the page (which numbers appear, which absences are named) against inputs
whose right answer is known by construction. The last two tests also cover the two ways
the tool is dangerous: a corrupted `curve.jsonl` line must not stop the summary, and an
unknown field must never be printed as `0`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "run_summary.py"


# --------------------------------------------------------------------------- fixtures


def run_tool(*args: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *map(str, args)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )


def read_page(directory: Path) -> str:
    return (directory / "end-summary.md").read_text(encoding="utf-8")


def train_meta(run_id: str, *, improver: str | None = "dsh") -> dict:
    meta = {
        "run_id": run_id,
        "mode": "unified",
        "harness": "loop",
        "dataset": "appworld",
        "agent_model": "qwen3.5-9b-local",
        "agent_backend": "openai",
        "sampling_policy": "all",
        "config": {
            "run": {"run_id": run_id, "side": "train", "rounds": 2, "trials": 1},
            "method": {"model": "deepseek-flash"},
            "improver": {"name": improver},
        },
        "split": {"train": ["a", "b", "c", "d"], "eval": ["e1", "e2", "e3"]},
    }
    if improver:
        meta["improver"] = {"name": improver, "version": "0.1.5+test", "model": "deepseek-flash"}
    return meta


def eval_meta(run_id: str, *, from_run: str, from_round: int) -> dict:
    meta = train_meta(run_id, improver=None)
    meta["config"]["run"]["side"] = "eval"
    meta["config"]["run"]["rounds"] = 1
    meta["evaluated_from"] = {"run_id": from_run, "round": from_round}
    return meta


def point(
    run_id: str,
    round_no: int,
    score: float | None,
    per_task: dict | None = None,
    *,
    side: str = "train",
    sha: str = "a" * 40,
    n_invalid: int | None = 0,
    n_timeout: int | None = None,
    n_scored: int | None = None,
    calls: int | None = 10,
    tokens: int | None = 1000,
    wall_clock_s: float | None = 100.0,
) -> dict:
    tasks = list(per_task or {})
    entry = {
        "run_id": run_id,
        "round": round_no,
        "side": side,
        "label": "H0 (base harness)" if round_no == 0 else "variant",
        "score": score,
        "identity": {"harness_name": "loop", "harness_sha": sha,
                     "agent_model": "qwen3.5-9b-local"},
        "sampling": {"dataset": "appworld", "policy": "all", "task_ids": tasks},
        "split": {"train": tasks, "eval": []},
        "per_task": dict(per_task or {}),
        "train_per_task": {},
    }
    if n_scored is not None:
        entry["n_scored"] = n_scored
    elif per_task:
        entry["n_scored"] = len(per_task)
    if n_invalid is not None:
        entry["n_invalid"] = n_invalid
    if n_timeout is not None:
        entry["n_timeout"] = n_timeout
    entry["cost"] = {
        "harness_model_calls": calls,
        "harness_tokens": tokens,
        "wall_clock_s": wall_clock_s,
    }
    entry["cumulative"] = {
        "wall_clock_s": (wall_clock_s or 0.0) * (round_no + 1),
        "evaluation_trials": len(per_task or {}),
    }
    return entry


def diff(round_no: int, files: list[dict]) -> dict:
    return {
        "round": round_no,
        "from": "f" * 40,
        "to": "e" * 40,
        "files": files,
        "added": sum(f.get("added", 0) for f in files),
        "removed": sum(f.get("removed", 0) for f in files),
    }


def write_run(root: Path, run_id: str, curve, *, meta: dict | None = None,
              diffs: dict | None = None, curve_text: str | None = None) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    if curve_text is not None:
        text = curve_text
    else:
        text = "\n".join(json.dumps(item) for item in curve) + ("\n" if curve else "")
    (run_dir / "curve.jsonl").write_text(text, encoding="utf-8")
    (run_dir / "run_meta.json").write_text(
        json.dumps(meta if meta is not None else train_meta(run_id), indent=1), encoding="utf-8")
    for name, payload in (diffs or {}).items():
        target = run_dir / "diffs" / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return run_dir


def test_partial_eval_summary_exposes_planned_and_scored_coverage(tmp_path):
    from tools import run_summary
    entry = point("partial", 0, .5, {"a": .5}, side="eval", n_invalid=1)
    entry["sampling"]["task_ids"] = ["a", "b", "c"]
    summary = run_summary.summarise(write_run(tmp_path, "partial", [entry]))
    coverage = summary["per_round"][0]
    assert coverage["planned"] == 3 and coverage["scored"] == 1
    assert coverage["unmeasured"] == 2 and coverage["invalid"] == 1
    rendered = run_summary.render_markdown(summary)
    assert "planned | scored" in rendered
    assert "may overlap" in rendered


def test_rejected_numeric_zero_keeps_summary_coverage_without_false_decline(tmp_path):
    from tools import run_summary
    rejected = point("rejected", 1, 0.0, {}, n_scored=0)
    rejected.update(measured_by_platform=False,
                    unmeasured={"reason": "candidate rejected", "problems": ["missing agent.py"]})
    rejected["sampling"]["task_ids"] = ["a", "b"]
    summary = run_summary.summarise(write_run(tmp_path, "rejected", [
        point("rejected", 0, .8, {"a": .8, "b": .8}), rejected]))
    assert summary["score"] == {"r0": .8, "last": None, "best": .8, "best_round": 0}
    assert summary["delta_total"] is None
    failure = summary["per_round"][-1]
    assert failure["score"] is None and failure["delta"] is None
    assert failure["scored"] == 0 and failure["planned"] == 2 and failure["unmeasured"] == 2
    assert failure["unmeasured_reason"] == rejected["unmeasured"]
    assert "candidate rejected" in run_summary.render_markdown(summary)


@pytest.mark.parametrize("marker", [{"measured_by_platform": False}, {"n_scored": 0},
    {"unmeasured_reason": "candidate rejected"}])
def test_summary_skips_explicitly_unmeasured_points_between_measurements(tmp_path, marker):
    from tools import run_summary
    rejected = point("gap", 1, 0.0)
    rejected.update(marker)
    summary = run_summary.summarise(write_run(tmp_path, "gap", [
        point("gap", 0, .8, {"a": .8}), rejected,
        point("gap", 2, .9, {"a": .9})]))
    assert summary["per_round"][1]["score"] is None
    assert summary["per_round"][2]["delta"] == pytest.approx(.1)
    assert summary["delta_total"] == pytest.approx(.1)


def test_summary_measured_zero_and_legacy_numeric_score_remain_results(tmp_path):
    from tools import run_summary
    legacy = point("zero", 0, .8)
    measured = point("zero", 1, 0.0, {"a": 0.0})
    measured["measured_by_platform"] = True
    summary = run_summary.summarise(write_run(tmp_path, "zero", [legacy, measured]))
    assert summary["score"]["r0"] == .8 and summary["score"]["last"] == 0.0
    assert summary["delta_total"] == pytest.approx(-.8)


def three_round_run(root: Path) -> Path:
    run_id = "synthetic-train"
    curve = [
        point(run_id, 0, 0.25, {"a": 0, "b": 0, "c": 0, "d": 1}, sha="a" * 40, calls=10, tokens=1000),
        point(run_id, 1, 0.50, {"a": 1, "b": 0, "c": 0, "d": 1}, sha="b" * 40, calls=20, tokens=2000),
        point(run_id, 2, 0.75, {"a": 1, "b": 1, "c": 0, "d": 1}, sha="c" * 40, calls=30, tokens=3000),
    ]
    diffs = {
        "round-1.json": diff(1, [{"path": "agent.py", "added": 10, "removed": 2},
                                 {"path": "docs/x.md", "added": 3, "removed": 0}]),
        "round-2.json": diff(2, [{"path": "agent.py", "added": 5, "removed": 1},
                                 {"path": "lib/y.py", "added": 0, "removed": 4}]),
    }
    return write_run(root, run_id, curve, diffs=diffs)


# ------------------------------------------------------------------------------ tests


def test_three_rounds_report_the_expected_numbers(tmp_path):
    run_dir = three_round_run(tmp_path)
    out = tmp_path / "out"

    result = run_tool(run_dir, "--out", out)
    assert result.returncode == 0, result.stderr
    assert "end-summary.md" in result.stdout

    summary = json.loads((out / "end-summary.json").read_text(encoding="utf-8"))
    assert summary["schema"] == "harnessgrad.run-summary.v1"
    assert summary["run_id"] == "synthetic-train"
    assert summary["dataset"] == "appworld"
    assert summary["side"] == "train"
    assert summary["harness"] == "loop"
    assert summary["improver"] == "dsh"
    assert summary["agent_model"] == "qwen3.5-9b-local"
    assert summary["harness_sha_first"] == "a" * 40
    assert summary["harness_sha_last"] == "c" * 40
    assert summary["rounds"] == 3

    assert summary["score"] == {"r0": 0.25, "last": 0.75, "best": 0.75, "best_round": 2}
    assert summary["delta_total"] == pytest.approx(0.5)
    assert [(e["round"], round(e["score"], 3), e["delta"]) for e in summary["per_round"]] == [
        (0, 0.25, None), (1, 0.5, pytest.approx(0.25)), (2, 0.75, pytest.approx(0.25)),
    ]
    assert [e["unmeasured"] for e in summary["per_round"]] == [0, 0, 0]
    assert [e["invalid"] for e in summary["per_round"]] == [0, 0, 0]

    assert summary["per_task"] is True
    assert summary["tasks"] == 4
    assert (summary["gained"], summary["lost"], summary["unchanged"]) == (2, 0, 2)

    assert [f["path"] for f in summary["changed_files"]] == ["agent.py", "lib/y.py", "docs/x.md"]
    assert [f["churn"] for f in summary["changed_files"]] == [18, 4, 3]
    assert summary["calls"] == 60
    assert summary["tokens"] == 6000

    page = read_page(out)
    assert page.startswith("# Run summary: synthetic-train")
    assert "r0 0.250 → r2 0.750 over 3 rounds, 4 tasks, 2 gained / 0 lost / 2 unchanged" in page
    assert "| 2 | 0.750 | +0.250 | 4 | 4 | 0 | 0 | — |" in page
    assert "`agent.py` +15 −3 = 18" in page
    assert "## What is missing" in page
    # --out must not have touched the run directory.
    assert not (run_dir / "end-summary.md").exists()
    assert not (run_dir / "end-summary.json").exists()


def test_missing_fields_are_named_absences_not_zeros(tmp_path):
    run_id = "sparse-run"
    curve = [
        # No `score` and no `n_invalid` anywhere: the run recorded neither.
        {"run_id": run_id, "round": 0, "side": "train",
         "identity": {"harness_name": "loop"}, "sampling": {"task_ids": ["a", "b"]}},
        {"run_id": run_id, "round": 1, "side": "train",
         "identity": {"harness_name": "loop"}, "sampling": {"task_ids": ["a", "b"]}},
    ]
    run_dir = write_run(tmp_path, run_id, curve)

    result = run_tool(run_dir)
    assert result.returncode == 0, result.stderr

    summary = json.loads((run_dir / "end-summary.json").read_text(encoding="utf-8"))
    assert summary["score"] == {"r0": None, "last": None, "best": None, "best_round": None}
    assert summary["delta_total"] is None
    assert summary["per_round"][0]["score"] is None
    assert summary["per_round"][0]["invalid"] is None
    assert summary["harness_sha_first"] is None
    assert summary["per_task"] is False
    assert summary["gained"] is None and summary["lost"] is None
    # The JSON itself must carry null, never a placeholder zero.
    payload = (run_dir / "end-summary.json").read_text(encoding="utf-8")
    assert '"best": null' in payload and '"invalid": null' in payload

    page = read_page(run_dir)
    assert "| 0 | — | — | 2 | — | — | — | — |" in page
    assert "0.000" not in page
    named = "\n".join(summary["notes"])
    assert "invalid" in named and "—" in named
    assert "score" in named and "no numeric score" in named
    assert "per_task" in named


def test_a_corrupted_curve_line_is_skipped_and_named(tmp_path):
    run_id = "corrupt-run"
    good = [
        point(run_id, 0, 0.1, {"a": 0, "b": 0}, n_invalid=0),
        point(run_id, 2, 0.3, {"a": 1, "b": 0}, n_invalid=0),
    ]
    curve_text = "\n".join([
        json.dumps(good[0]),
        '{"run_id": "corrupt-run", "round": 1, "score": 0.',   # truncated line
        json.dumps(good[1]),
        "[1, 2, 3]",                                            # valid JSON, not a point
        "",
    ])
    run_dir = write_run(tmp_path, run_id, None, curve_text=curve_text)

    result = run_tool(run_dir)
    assert result.returncode == 0, result.stderr

    summary = json.loads((run_dir / "end-summary.json").read_text(encoding="utf-8"))
    assert summary["rounds"] == 2
    assert [e["round"] for e in summary["per_round"]] == [0, 2]
    assert summary["score"]["r0"] == 0.1
    assert summary["score"]["last"] == 0.3
    corruption = [n for n in summary["notes"]
                  if "curve.jsonl" in n and "could not be parsed" in n]
    assert corruption, summary["notes"]
    assert "2 line(s)" in corruption[0]
    assert "2 line(s)" in read_page(run_dir)


def test_all_writes_every_run_and_dry_run_writes_nothing(tmp_path):
    runs = tmp_path / "runs"
    training = three_round_run(runs)
    exam = write_run(
        runs, "synthetic-eval",
        [point("synthetic-eval", 0, 0.5, {"e1": 1, "e2": 0}, side="eval")],
        meta=eval_meta("synthetic-eval", from_run="synthetic-train", from_round=2),
    )
    (runs / "not-a-run").mkdir()
    (runs / "not-a-run" / "notes.txt").write_text("no curve here\n", encoding="utf-8")

    dry = run_tool("--all", runs, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "would write" in dry.stdout
    for run_dir in (training, exam):
        assert not (run_dir / "end-summary.md").exists()
        assert not (run_dir / "end-summary.json").exists()
    assert not (runs / "not-a-run" / "end-summary.md").exists()

    real = run_tool("--all", runs)
    assert real.returncode == 0, real.stderr
    for run_dir in (training, exam):
        assert (run_dir / "end-summary.md").is_file()
        assert (run_dir / "end-summary.json").is_file()
        assert "What is missing" in read_page(run_dir)
    assert not (runs / "not-a-run" / "end-summary.md").exists()

    # `--all --out` keeps the archive clean by nesting one directory per run id.
    elsewhere = tmp_path / "elsewhere"
    moved = run_tool("--all", runs, "--out", elsewhere)
    assert moved.returncode == 0, moved.stderr
    assert (elsewhere / "synthetic-train" / "end-summary.md").is_file()
    assert (elsewhere / "synthetic-eval" / "end-summary.json").is_file()


def test_a_replacement_is_reported_not_silent(tmp_path):
    run_dir = three_round_run(tmp_path)
    first = run_tool(run_dir)
    assert first.returncode == 0, first.stderr
    assert "wrote" in first.stdout

    curve_path = run_dir / "curve.jsonl"
    points = [json.loads(line) for line in curve_path.read_text().splitlines() if line.strip()]
    points[-1]["score"] = 0.9
    curve_path.write_text("\n".join(json.dumps(p) for p in points) + "\n", encoding="utf-8")

    second = run_tool(run_dir)
    assert second.returncode == 0, second.stderr
    assert "replaced" in second.stdout
    assert "previous sha256" in second.stdout
    assert json.loads((run_dir / "end-summary.json").read_text())["score"]["last"] == 0.9


def test_a_long_curve_keeps_the_page_short(tmp_path):
    run_id = "long-run"
    curve = []
    for round_no in range(30):
        tasks = {f"t{i}": 1 if i < round_no else 0 for i in range(30)}
        curve.append(point(run_id, round_no, round_no / 30, tasks,
                           sha=f"{round_no:040x}", n_invalid=0))
    run_dir = write_run(tmp_path, run_id, curve)

    result = run_tool(run_dir)
    assert result.returncode == 0, result.stderr

    page = read_page(run_dir)
    assert len(page.splitlines()) <= 42, len(page.splitlines())
    assert "per-round table shows 20 of 30 rounds; every row is in end-summary.json" in page
    # The middle of the curve is elided in the page, not in the JSON.
    assert len(json.loads((run_dir / "end-summary.json").read_text())["per_round"]) == 30


def test_eval_single_point_does_not_invent_rounds(tmp_path):
    run_id = "synthetic-exam"
    curve = [point(run_id, 0, 0.38596491228070173, {"e1": 1, "e2": 0, "e3": 1}, side="eval")]
    run_dir = write_run(
        tmp_path, run_id, curve,
        meta=eval_meta(run_id, from_run="synthetic-train", from_round=20),
    )

    result = run_tool(run_dir)
    assert result.returncode == 0, result.stderr

    summary = json.loads((run_dir / "end-summary.json").read_text(encoding="utf-8"))
    assert summary["side"] == "eval"
    assert summary["rounds"] == 1
    assert len(summary["per_round"]) == 1
    assert summary["per_round"][0]["round"] == 0
    assert summary["score"]["r0"] == pytest.approx(0.38596491228070173)
    assert summary["score"]["last"] == summary["score"]["r0"]
    assert summary["score"]["best"] == summary["score"]["r0"]
    assert summary["score"]["best_round"] == 0
    # One point is not a trend: a delta of 0 would be a claim nothing supports.
    assert summary["delta_total"] is None
    assert summary["gained"] is None and summary["lost"] is None and summary["unchanged"] is None
    assert summary["changed_files"] == []
    assert summary["evaluated_from"] == {"run_id": "synthetic-train", "round": 20}
    assert any("one curve point" in note for note in summary["notes"])

    page = read_page(run_dir)
    assert "single exam point r0 0.386, 3 tasks" in page
    assert "synthetic-train @ round 20" in page
    assert "over 1 rounds" not in page
    assert "| 0 | 0.386 | — | 3 | 3 | 0 | 0 | — |" in page
    assert "diffs:" in page  # named absence, not "0 changed files"
    assert not (run_dir / "diffs").exists()
