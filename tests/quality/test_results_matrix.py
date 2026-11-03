"""The exportable result matrix: one row per run, read from its own curve.

These tests build synthetic run roots rather than touching the real archive. The
point of each one is a distinction the tool must not lose:

* a row's delta is arithmetic on that run's own points, and a descending run is
  allowed to say so;
* a run without a curve, a run that is still in flight, a run whose curve is corrupt
  and a run that was measured but not scored are four different skips -- and none of
  them becomes a `0.000` row;
* an eval-side measurement is one point, so it has no trend: `rounds: 1` and a delta
  of `--`, not `0.0`;
* `--dataset` / `--side` / `--min-rounds` select, and the written JSON re-parses;
* the JSON is byte-identical across runs, because it has no timestamp in it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import results_matrix


# ------------------------------------------------------------------------- fixtures

def _point(run_id: str, round_no: int, score, *, side: str, dataset: str,
           harness: str, model: str, tasks: int, calls: int, invalid: int,
           pool: str | None) -> dict:
    return {
        "run_id": run_id,
        "round": round_no,
        "side": side,
        "score": score,
        "identity": {"harness_name": harness, "agent_model": model},
        "sampling": {
            "dataset": dataset,
            "policy": "all",
            "task_ids": [f"t{i}" for i in range(tasks)],
            "selected_within": pool if pool is not None else side,
        },
        "per_task": {f"t{i}": score for i in range(tasks)},
        "n_scored": tasks,
        "n_invalid": invalid,
        "cost": {"harness_model_calls": calls},
    }


def make_run(root: Path, run_id: str, *, method: str = "ahe",
             dataset: str = "appworld", harness: str = "loop", side: str = "train",
             scores=(0.2, 0.5, 0.4), model: str = "qwen3.5-9b-local",
             calls=10, invalid=0, tasks: int = 4, pool: str | None = None,
             curve: bool = True, entrypoint: bool = True,
             evaluated_from: str | None = None) -> Path:
    """A run directory shaped the way the platform writes one."""
    run = root / run_id
    run.mkdir(parents=True)
    meta = {"run_id": run_id, "harness": harness, "dataset": dataset,
            "agent_model": model}
    if entrypoint:
        meta["method_entrypoint"] = f"python3 /opt/harnessgrad/methods/{method}/run.py"
    if evaluated_from is not None:
        meta["evaluated_from"] = {"run_id": evaluated_from, "round": 0}
    (run / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if curve:
        lines = [json.dumps(_point(run_id, i, score, side=side, dataset=dataset,
                                   harness=harness, model=model, tasks=tasks,
                                   calls=calls, invalid=invalid, pool=pool))
                 for i, score in enumerate(scores)]
        (run / "curve.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run


def write_header(run: Path, n_tasks: int) -> None:
    """The one event a run writes before it has any point."""
    (run / "events.jsonl").write_text(
        json.dumps({"seq": 1, "kind": "header", "n_tasks": n_tasks}) + "\n",
        encoding="utf-8")


def write_progress(run: Path, round_no: int, task_ids: list[str]) -> None:
    """`progress/round-N/<task>.json`: written task by task, so a count is a cursor."""
    directory = run / "progress" / f"round-{round_no}"
    directory.mkdir(parents=True)
    for task_id in task_ids:
        (directory / f"{task_id}.json").write_text(
            json.dumps({"task_id": task_id, "round": round_no, "complete": True}),
            encoding="utf-8")


def _cells(markdown: str, run_id: str) -> dict[str, str]:
    """The markdown row for one run, keyed by column name."""
    header = next(line for line in markdown.splitlines() if line.startswith("| run_id"))
    columns = [cell.strip() for cell in header.strip("|").split("|")]
    line = next(line for line in markdown.splitlines() if line.startswith(f"| {run_id}"))
    values = [cell.strip() for cell in line.strip("|").split("|")]
    return dict(zip(columns, values))


# ---------------------------------------------------------------------------- tests

def test_two_methods_two_datasets_three_rounds(tmp_path):
    """The matrix the ask is about, with the arithmetic asserted per row.

    The gaia row **ends below where it started** (0.60 -> 0.45), so it is the case
    that proves a delta is allowed to be negative. It is `last_delta_pp` that goes
    negative: `best - r0` cannot, because `best` is a maximum over the very points
    that include `r0`.
    """
    root = tmp_path / "runs"
    make_run(root, "ahe-appworld", method="ahe", dataset="appworld",
             scores=(0.20, 0.50, 0.40), calls=10, invalid=0)
    make_run(root, "ahe-gaia", method="ahe", dataset="gaia",
             scores=(0.60, 0.30, 0.45), calls=10)
    make_run(root, "dgm-appworld", method="dgm", dataset="appworld",
             scores=(0.10, 0.00, 0.25), calls=10)
    make_run(root, "dgm-gaia", method="dgm", dataset="gaia",
             scores=(0.80, 0.75, 0.70), calls=10)

    payload, duplicates = results_matrix.build_matrix([str(root)])
    assert duplicates == []
    rows = payload["rows"]

    # Sorted by (method, dataset, harness, run id) -- not by discovery order.
    assert [r["run_id"] for r in rows] == [
        "ahe-appworld", "ahe-gaia", "dgm-appworld", "dgm-gaia"]
    by_id = {r["run_id"]: r for r in rows}

    rising = by_id["ahe-appworld"]
    assert rising["method"] == "ahe"
    assert rising["dataset"] == "appworld"
    assert rising["harness"] == "loop"
    assert rising["side"] == "train"
    assert rising["pool"] == "train"
    assert rising["agent_model"] == "qwen3.5-9b-local"
    assert rising["rounds"] == 3
    assert rising["r0"] == pytest.approx(0.20)
    assert rising["last"] == pytest.approx(0.40)
    assert rising["best"] == pytest.approx(0.50)
    assert rising["best_round"] == 1
    assert rising["delta_pp"] == pytest.approx(30.0)
    assert rising["delta"] == pytest.approx(0.30)
    assert rising["last_delta_pp"] == pytest.approx(20.0)
    assert rising["invalid"] == 0
    assert rising["calls"] == 10 * 3
    assert rising["n_tasks"] == 4

    declining = by_id["ahe-gaia"]
    assert declining["best"] == pytest.approx(0.60)      # r0 itself is the best
    assert declining["best_round"] == 0                  # tie/earliest wins
    assert declining["delta_pp"] == pytest.approx(0.0)
    assert declining["last_delta_pp"] == pytest.approx(-15.0)
    assert declining["last_delta"] == pytest.approx(-0.15)
    assert declining["last_delta_pp"] < 0

    fell_then_rose = by_id["dgm-appworld"]
    assert (fell_then_rose["r0"], fell_then_rose["last"],
            fell_then_rose["best"]) == (pytest.approx(0.10), pytest.approx(0.25),
                                        pytest.approx(0.25))
    assert fell_then_rose["best_round"] == 2
    assert fell_then_rose["delta_pp"] == pytest.approx(15.0)

    never_moved = by_id["dgm-gaia"]
    assert never_moved["delta_pp"] == pytest.approx(0.0)
    assert never_moved["last_delta_pp"] == pytest.approx(-10.0)

    # A best-based delta is non-negative by construction for every run.
    assert all(r["delta_pp"] >= 0 for r in rows)


def test_unsummarisable_runs_are_listed_and_never_zeroed(tmp_path):
    """Five ways to have no row -- each with its own reason, none with a score."""
    root = tmp_path / "runs"

    no_curve = make_run(root, "no-curve", curve=False)
    write_header(no_curve, 4)

    running = make_run(root, "running", curve=False)
    write_header(running, 4)
    write_progress(running, 0, ["t0", "t1"])          # 2 of 4 tasks, no point yet

    corrupt = make_run(root, "corrupt", scores=(0.5,))
    with (corrupt / "curve.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{ this is not json\n")

    empty = make_run(root, "empty", curve=False)
    (empty / "curve.jsonl").write_text("\n", encoding="utf-8")

    unmeasured = make_run(root, "unmeasured", scores=(None,))
    (unmeasured / "curve.jsonl").write_text(
        json.dumps(_point("unmeasured", 0, None, side="eval", dataset="appworld",
                          harness="loop", model="qwen3.5-9b-local", tasks=2,
                          calls=1, invalid=2, pool="eval")) + "\n",
        encoding="utf-8")

    payload, _ = results_matrix.build_matrix([str(root)])

    assert payload["rows"] == []                       # never a 0.000 stand-in
    reasons = {entry["run_id"]: entry["reason"] for entry in payload["skipped"]}
    assert reasons == {
        "no-curve": "no_curve",
        "running": "running",
        "corrupt": "corrupt_curve",
        "empty": "empty_curve",
        "unmeasured": "unmeasured",
    }
    assert len(set(reasons.values())) == len(reasons)  # distinct, not one bucket
    for entry in payload["skipped"]:
        assert entry["detail"]
        assert "score" not in entry                    # a skip carries no score field
    assert "line 2" in next(e for e in payload["skipped"]
                            if e["run_id"] == "corrupt")["detail"]

    markdown = results_matrix.render_markdown(payload)
    # Each of them appears in the skipped table and nowhere above it: no row table
    # ever printed one of these runs as a zero-scored participant.
    below = markdown.index("## Runs that could not be summarised")
    for run_id in reasons:
        assert run_id not in markdown[:below]
        assert run_id in markdown[below:]
    assert "no_curve" in markdown and "corrupt_curve" in markdown


def test_eval_single_point_has_rounds_one_and_no_delta(tmp_path):
    """One measured point is an eval, not a flat curve: its delta is `—`, not 0."""
    root = tmp_path / "runs"
    make_run(root, "eval-one", side="eval", scores=(0.35,), pool="eval")

    payload, _ = results_matrix.build_matrix([str(root)])
    (row,) = payload["rows"]
    assert row["rounds"] == 1
    assert row["side"] == "eval"
    assert row["r0"] == row["last"] == row["best"] == pytest.approx(0.35)
    assert row["best_round"] == 0
    assert row["delta_pp"] is None
    assert row["delta"] is None
    assert row["last_delta_pp"] is None

    cells = _cells(results_matrix.render_markdown(payload), "eval-one")
    assert cells["rounds"] == "1"
    assert cells["delta_pp"] == results_matrix.DASH
    assert cells["last_delta_pp"] == results_matrix.DASH
    assert cells["r0"] == "35.0"

    # The JSON is where a machine reads it: null, never 0.0.
    data = json.loads(json.dumps(payload))
    assert data["rows"][0]["delta_pp"] is None


def test_dataset_and_side_filters_and_written_files_reparse(tmp_path):
    root = tmp_path / "runs"
    make_run(root, "ahe-appworld", method="ahe", dataset="appworld", side="train")
    make_run(root, "ahe-gaia-eval", method="ahe", dataset="gaia", side="eval",
             scores=(0.5,))
    make_run(root, "dgm-appworld-eval", method="dgm", dataset="appworld", side="eval",
             scores=(0.5,))

    by_dataset, _ = results_matrix.build_matrix([str(root)], dataset="appworld")
    assert [r["run_id"] for r in by_dataset["rows"]] == [
        "ahe-appworld", "dgm-appworld-eval"]
    assert {r["dataset"] for r in by_dataset["rows"]} == {"appworld"}

    by_side, _ = results_matrix.build_matrix([str(root)], side="eval")
    assert [r["run_id"] for r in by_side["rows"]] == [
        "ahe-gaia-eval", "dgm-appworld-eval"]
    assert {r["side"] for r in by_side["rows"]} == {"eval"}

    markdown_path = tmp_path / "out" / "matrix.md"
    json_path = tmp_path / "out" / "matrix.json"
    assert results_matrix.main(["--runs", str(root), "--md", str(markdown_path),
                                "--json", str(json_path)]) == 0

    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["schema"] == results_matrix.SCHEMA
    assert written["generated_from"] == [str(root)]
    assert len(written["rows"]) == 3
    assert len(written["skipped"]) == 0

    text = markdown_path.read_text(encoding="utf-8")
    assert text.startswith("# HarnessGrad results matrix")
    assert "## method: ahe" in text and "## method: dgm" in text
    for run_id in ("ahe-appworld", "ahe-gaia-eval", "dgm-appworld-eval"):
        assert run_id in text

    # `--group-by harness` regroups the same rows without changing them.
    grouped = results_matrix.render_markdown(written, group_by="harness")
    assert "## harness: loop" in grouped
    assert "## method:" not in grouped


def test_min_rounds_filters_training_runs_only(tmp_path):
    root = tmp_path / "runs"
    make_run(root, "two-round", method="ahe", dataset="appworld", side="train",
             scores=(0.2, 0.3))
    make_run(root, "three-round", method="ahe", dataset="appworld", side="train",
             scores=(0.2, 0.3, 0.4))
    make_run(root, "eval-one", method="ahe", dataset="appworld", side="eval",
             scores=(0.7,))

    payload, _ = results_matrix.build_matrix([str(root)], min_rounds=3)
    # Sorted by (method, dataset, harness, run id): eval-one < three-round.
    assert [r["run_id"] for r in payload["rows"]] == ["eval-one", "three-round"]
    # The eval run has one point and must survive: --min-rounds filters *studies*.
    assert {r["side"] for r in payload["rows"]} == {"train", "eval"}
    (skipped,) = payload["skipped"]
    assert skipped["run_id"] == "two-round"
    assert skipped["reason"] == "below_min_rounds"


def test_method_comes_from_the_record_not_from_a_guess(tmp_path):
    """`identity.method`, `evaluated_from` and the run-id template are fallbacks,
    and the row says which one answered."""
    root = tmp_path / "runs"
    make_run(root, "appworld-study-dgm-20261020T0414Z", method="dgm",
             scores=(0.1, 0.2))
    make_run(root, "appworld-dev-dgm-r0-20261020T0415Z", method="dgm",
             entrypoint=False, evaluated_from="appworld-study-dgm-20261020T0414Z",
             scores=(0.2,))
    make_run(root, "appworld-dev-ahe-r0-20261020T0416Z", method="ahe",
             entrypoint=False, scores=(0.3,))
    # No entrypoint and a name neither template recognises: only the point's own
    # `identity.method` can name this run's method.
    identity_run = make_run(root, "study-without-an-entrypoint", method="sica_ci",
                            entrypoint=False, scores=(0.4,))
    (identity_run / "curve.jsonl").write_text(
        json.dumps({**json.loads((identity_run / "curve.jsonl").read_text()),
                    "identity": {"harness_name": "loop", "agent_model": "m",
                                 "method": "sica_ci"}}) + "\n",
        encoding="utf-8")

    payload, _ = results_matrix.build_matrix([str(root)])
    by_id = {r["run_id"]: r for r in payload["rows"]}
    assert by_id["study-without-an-entrypoint"]["method"] == "sica_ci"
    assert by_id["study-without-an-entrypoint"]["method_source"] == "identity"
    # The study run reads `identity.method` from its own points first.
    assert by_id["appworld-study-dgm-20261020T0414Z"]["method"] == "dgm"
    assert by_id["appworld-study-dgm-20261020T0414Z"]["method_source"] == "entrypoint"
    assert by_id["appworld-dev-dgm-r0-20261020T0415Z"]["method"] == "dgm"
    assert by_id["appworld-dev-dgm-r0-20261020T0415Z"]["method_source"] == "evaluated_from"
    assert by_id["appworld-dev-ahe-r0-20261020T0416Z"]["method"] == "ahe"
    assert by_id["appworld-dev-ahe-r0-20261020T0416Z"]["method_source"] == "run_id"


def test_json_is_byte_identical_across_runs(tmp_path):
    """No timestamp inside the JSON, so two exports of one archive are one file."""
    root = tmp_path / "runs"
    make_run(root, "ahe-appworld", method="ahe", dataset="appworld",
             scores=(0.2, 0.4, 0.3))
    make_run(root, "dgm-gaia", method="dgm", dataset="gaia", scores=(0.5,))
    no_curve = make_run(root, "no-curve", curve=False)
    write_header(no_curve, 3)

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first_md = tmp_path / "first.md"
    second_md = tmp_path / "second.md"
    for json_path, md_path in ((first, first_md), (second, second_md)):
        assert results_matrix.main(["--runs", str(root), "--json", str(json_path),
                                    "--md", str(md_path)]) == 0

    assert first.read_bytes() == second.read_bytes()
    assert first_md.read_bytes() == second_md.read_bytes()
    assert b"timestamp" not in first.read_bytes()
