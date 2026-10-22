"""Selected Chess controls and failure evidence, using fabricated puzzles only."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import chess
import pytest

import data.datasets.chessbench_puzzles as dataset
from data.scaffolds.qa import control


@pytest.fixture(autouse=True)
def forbid_full_pool(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("selected controls must not load the full Chess pool")

    monkeypatch.setattr(dataset, "_read_rows", forbidden)
    monkeypatch.setattr(dataset, "_rows", forbidden)
    monkeypatch.setattr(dataset, "load", forbidden)
    monkeypatch.setattr(dataset._SPEC, "ensure", forbidden)
    monkeypatch.setattr(dataset._SPEC, "build", forbidden)


@pytest.fixture
def synthetic_csv(monkeypatch, tmp_path):
    path = tmp_path / "puzzles.csv"
    rows = [{"PuzzleId": f"fabricated-{index}", "Rating": "0",
             "FEN": chess.STARTING_FEN,
             "Moves": "e2e4 e7e5 g1f3 b8c6 f1b5"} for index in range(12)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(dataset, "CSV_PATH", path)
    return rows


def test_selected_loading_stops_at_last_requested_row_and_preserves_ids(
    monkeypatch, synthetic_csv,
):
    original_reader = csv.DictReader
    original_row = dataset._row
    visited, decoded = [], []

    def reader(handle):
        for index, row in enumerate(original_reader(handle)):
            assert index <= 5, "read past the last selected puzzle"
            visited.append(index)
            if index not in {2, 5}:
                row["FEN"] = "invalid unselected board"
                row["Moves"] = "invalid unselected moves"
                row["Rating"] = "invalid unselected rating"
            yield row

    def decode(row):
        decoded.append(row["PuzzleId"])
        return original_row(row)

    monkeypatch.setattr(csv, "DictReader", reader)
    monkeypatch.setattr(dataset, "_row", decode)
    cache = [{"task_id": "unrelated-full-pool-cache"}]
    monkeypatch.setattr(dataset._SPEC, "_records", cache)
    monkeypatch.setattr(dataset._SPEC, "load_error", "prior full-pool failure")
    monkeypatch.setattr(dataset, "_BUILT", None)
    ids = ["chessbench_puzzles/test/5", "chessbench_puzzles/test/2"]
    tasks, scorable = dataset.load_selected([*ids, ids[0]])
    assert [task["task_id"] for task in tasks] == ids
    assert scorable == {task_id: "e7e5 b8c6" for task_id in ids}
    assert visited == list(range(6))
    assert decoded == ["fabricated-2", "fabricated-5"]
    assert dataset._SPEC._records is cache
    assert dataset._SPEC.load_error == "prior full-pool failure"
    assert dataset._BUILT is None
    assert all(set(task) == {"task_id", "goal", "inputs"} for task in tasks)
    assert all("fabricated-" not in task["goal"] for task in tasks)


@pytest.mark.parametrize("selection", [
    [], ["chessbench_puzzles/train/0"], ["other/test/0"],
    ["chessbench_puzzles/test/-1"], ["chessbench_puzzles/test/10000"],
    ["chessbench_puzzles/test/01"], ["chessbench_puzzles/test/0/trailing"],
    ["chessbench_puzzles/test/0", "chessbench_puzzles/test/10000"],
])
def test_invalid_selection_is_rejected_before_any_csv_read(monkeypatch, selection):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid selection opened the CSV")

    monkeypatch.setattr(dataset, "CSV_PATH", SimpleNamespace(open=forbidden))
    with pytest.raises(ValueError):
        dataset.load_selected(selection)


def test_missing_selected_index_is_explicit(synthetic_csv):
    with pytest.raises(ValueError, match="missing selected indices.*12"):
        dataset.load_selected(["chessbench_puzzles/test/12"])


@pytest.mark.parametrize("task_id", ["chessbench_puzzles/test/7", None])
def test_control_record_uses_selected_reader(synthetic_csv, task_id):
    record = control._record(dataset, task_id)
    if task_id is None:
        assert dataset.base.is_eval(record["task_id"])
    else:
        assert record["task_id"] == task_id
    index = int(record["task_id"].rsplit("/", 1)[1])
    assert record["index"] == index
    assert record["gold"]["puzzle_id"] == f"fabricated-{index}"
    assert record["gold"]["answer"] == "e7e5 b8c6"


@pytest.mark.parametrize("oracle", [True, False])
def test_targeted_local_control_never_reads_full_pool(synthetic_csv, tmp_path, oracle):
    workdir = tmp_path / "local-control"
    assert control.main([
        "--dataset", "chessbench_puzzles", "--oracle" if oracle else "--noop",
        "--task-id", "chessbench_puzzles/test/7", "--workdir", str(workdir),
    ]) == 0
    assert float((workdir / "verifier/reward.txt").read_text()) == float(oracle)
    evidence = json.loads((workdir / "verifier/evidence.json").read_text())
    assert evidence["measured"] is True
    assert evidence["gold"] == {"redacted": True}


@pytest.mark.parametrize("oracle", [True, False])
def test_targeted_platform_control_passes_selected_specs_to_runner(
    monkeypatch, synthetic_csv, tmp_path, oracle,
):
    calls = []
    task_id = "chessbench_puzzles/test/7"
    record = dataset.control_task(task_id)

    def run_one(**kwargs):
        calls.append(kwargs)
        assert kwargs["task"]["task_id"] == task_id
        assert kwargs["task"]["goal"] == dataset._goal(record["question"], record["gold"])
        assert kwargs["env"] == {"kind": "files"}
        assert kwargs["verifier"] == dataset.verify_for(record)
        # Exercise the supplied setup and the real verifier without loading a model
        # or entering the platform's mount namespace.
        episode = tmp_path / "episode"
        episode.mkdir()
        for entry in kwargs["setup"]["files"]:
            target = episode / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if "content" in entry:
                target.write_text(entry["content"])
            else:
                shutil.copy2(entry["from"], target)
        if oracle:
            (episode / "answer.txt").write_text((episode / ".oracle/answer.txt").read_text())
        else:
            assert not (episode / ".oracle/answer.txt").exists()
        reward, _ = control.run_check(dataset, record, episode)
        return {"exit_code": 0, "verdict": {"score": reward, "passed": bool(reward)}}

    monkeypatch.setitem(sys.modules, "eval.run.runner", SimpleNamespace(run_one=run_one))
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path / "initial-work-root"))
    assert control.main([
        "--dataset", "chessbench_puzzles", "--oracle" if oracle else "--noop",
        "--platform", "--task-id", task_id,
        "--workdir", str(tmp_path / "platform-control"),
    ]) == 0
    assert len(calls) == 1


@pytest.fixture
def staged_verifier(tmp_path):
    gold = {"fen": chess.STARTING_FEN,
            "moves": ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
            "answer": "e7e5 b8c6", "puzzle_id": "fabricated-private-identifier"}
    spec = dataset.verify_for({"task_id": "synthetic-chessbench-selected-control", "gold": gold})
    for entry in spec["inputs"]:
        target = tmp_path / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    (tmp_path / "answer.txt").write_text("invalid synthetic answer")
    return tmp_path, gold, spec


def assert_redacted_failure(staged_verifier, expected_error):
    workdir, gold, spec = staged_verifier
    proc = subprocess.run(
        [sys.executable, *spec["argv"][1:]], cwd=workdir,
        env={**os.environ, "PYTHONPATH": str(workdir)},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert not (workdir / "verifier/reward.txt").exists()
    evidence_text = (workdir / "verifier/evidence.json").read_text()
    evidence = json.loads(evidence_text)
    assert evidence["measured"] is False
    assert expected_error in evidence["error"]
    assert evidence["gold"] == {"redacted": True}
    for key in ("fen", "moves", "puzzle_id"):
        assert f'"{key}"' not in evidence_text
    assert gold["fen"] not in evidence_text
    assert gold["puzzle_id"] not in evidence_text
    assert all(move not in evidence_text for move in gold["moves"])


@pytest.mark.parametrize("prediction,expected_error", [
    (b"{", "malformed"), (b"\xff", "malformed"),
    (b"[]", "invalid"),
    (b'{"schema_version":1,"answer":17}', "invalid"),
    (b'{"schema_version":1,"answer":"test","puzzle_id":"fabricated"}', "invalid"),
])
def test_malformed_prediction_evidence_redacts_private_reference(
    staged_verifier, prediction, expected_error,
):
    (staged_verifier[0] / "prediction.json").write_bytes(prediction)
    assert_redacted_failure(staged_verifier, expected_error)


@pytest.mark.parametrize("failure", ["missing_adapter", "missing_chess_dependency"])
def test_import_failure_evidence_redacts_private_reference(staged_verifier, failure):
    workdir = staged_verifier[0]
    if failure == "missing_adapter":
        (workdir / "data/datasets/chessbench_puzzles.py").unlink()
    else:
        (workdir / "chess.py").write_text(
            'raise ImportError("synthetic unavailable chess dependency")\n')
    assert_redacted_failure(staged_verifier, "could not import data.datasets.chessbench_puzzles")
