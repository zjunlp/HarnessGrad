"""DS-1000 official evaluator: verbatim construction, measured coverage, offline data.

Pinned source: xlang-ai/DS-1000@b39aab71, `test_ds1000.py:33-40` and `execution.py:17-77`.
The official runner conditionally calls `test_string` only when the context declares it,
runs each candidate in its own process, and keeps `reliability_guard` disabled.

Three facts this file pins rather than assumes:

* **47 rows are excluded because their official test code imports tensorflow**, which this
  platform does not provide and which the sandbox cannot reach from outside the work root.
  The group is recomputed from `code_context`, so the criterion cannot drift.
* **27 rows are excluded because the released gold targets an older library stack.** The
  cause of each is frozen in `MEASURED_EXCLUSIONS`, cross-checked against the recorded full
  sweep, and re-observed here by running the real check against the excluded row.
* **14 rows call `sns.load_dataset(...)`** and are *included*, because the four CSVs seaborn
  needs are a pinned download shipped in `VERIFY.inputs` and pointed at with `SEABORN_DATA`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import data.datasets.ds1000 as dataset

ROOT = Path(__file__).resolve().parents[2]
SOURCE = dataset.SOURCE_DIR / "test_ds1000.py"
EXECUTION = dataset.SOURCE_DIR / "execution.py"
SWEEP = dataset.DATASET_ROOT / "_src" / "oracle-sweep.json"

#: The failure text the runner reports for each frozen cause. A frozen reason has to remain
#: re-observable, or the table is history rather than a claim.
CAUSE_PATTERNS = {
    "pandas2-append": r"'DataFrame' object has no attribute 'append'",
    "pandas2-str-split-positional": r"StringMethods\.split\(\) takes from 1 to 2",
    "pandas2-str-rsplit-positional": r"StringMethods\.rsplit\(\) takes from 1 to 2",
    "pandas2-drop-positional": r"DataFrame\.drop\(\) takes from 1 to 2",
    "pandas2-set-axis-inplace": r"set_axis\(\) got an unexpected keyword argument 'inplace'",
    "pandas2-date-format": r"format='mixed'",
    "pandas2-dtype-compare": r"not supported between instances of 'str' and 'int'",
    "pandas2-index-insert": r"incompatible index of inserted column",
    "scipy-dok-update": r"'dok_matrix' object has no attribute '_update'",
    "scipy-triu-moved": r"cannot import name 'triu' from 'scipy.linalg'",
    "missing-xgboost": r"No module named 'xgboost'",
    "network-fetch-403": r"HTTP Error 403",
}


def official_program(row: dict, candidate: str) -> str:
    return (row["code_context"] + "\n" + f"code = {candidate!r}\n"
            + "test_execution(code)\n"
            + ("test_string(code)\n" if "test_string(" in row["code_context"] else ""))


def _control(index: int, arm: str) -> subprocess.CompletedProcess:
    """The sanctioned by-hand arm, for rows `load()` offers."""
    return subprocess.run(
        [sys.executable, "data/datasets/ds1000/control.py", arm, "--task-id", dataset.task_id(index)],
        cwd=ROOT, capture_output=True, text=True, timeout=300)


def _run_check_directly(index: int, tmp_path: Path) -> tuple[float, dict]:
    """Run the real `verify/check.py` for any row, including an excluded one.

    The control reaches only rows `load()` offers, so an excluded row's recorded failure is
    re-observed by materialising its workdir the way the platform does -- `SETUP` files plus
    re-materialised `VERIFY.inputs`, then the reference solution -- and running the same check
    program the platform runs.
    """
    from data.scaffolds import command_control

    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    for entry in dataset.setup_for(index)["files"]:
        target = work / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    verifier = dataset.verify_for(index)
    for entry in verifier["inputs"]:
        target = work / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            source = Path(entry["from"])
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
    (work / "solution.py").write_text(dataset._raw()[index]["reference_code"],
                                      encoding="utf-8")
    env = dict(os.environ,
               PYTHONPATH=str(work) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=work,
                          capture_output=True, text=True, env=env, timeout=300)
    reward_path = work / command_control.REWARD_REL
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = work / command_control.EVIDENCE_REL
    evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
    return reward, {"stdout": proc.stdout, "stderr": proc.stderr, **evidence}


def test_pinned_official_source_contains_the_scoring_construction():
    assert SOURCE.is_file() and EXECUTION.is_file()
    source = SOURCE.read_text(encoding="utf-8")
    assert "p['code_context'] + '\\n'" in source
    assert "f'code = {repr(answers[id])}\\n'" in source
    assert "'test_string(code)\\n'  if 'test_string(' in p['code_context']" in source
    execution = EXECUTION.read_text(encoding="utf-8")
    assert "def check_correctness(program: str, timeout: float" in execution
    assert "p.join(timeout=timeout + 1)" in execution
    # Upstream deliberately leaves the guard disabled: data-science libraries need the
    # filesystem. Re-enabling it here would change which tasks can pass at all.
    assert "# reliability_guard()" in execution


def test_the_release_is_covered_exactly_once_by_the_two_exclusion_groups():
    rows = dataset._read()
    excluded = dataset.excluded_indices()
    included = dataset.included_indices()
    assert len(rows) == 1000
    assert len(excluded) == 74
    assert len(included) == 926
    assert set(included) | excluded == set(range(1000))
    assert not (set(included) & excluded)
    assert dataset.tensorflow_indices() == {
        i for i, row in enumerate(rows) if dataset._TF_PATTERN.search(row["code_context"])}
    assert len(dataset.tensorflow_indices()) == 47
    assert len(dataset.measured_exclusions()) == 27
    assert not (dataset.tensorflow_indices() & set(dataset.measured_exclusions()))


def test_the_tensorflow_group_is_selected_by_the_import_not_the_label():
    rows = dataset._read()
    labels = {rows[i]["metadata"]["library"] for i in dataset.tensorflow_indices()}
    # Two tensorflow-conversion rows are labelled `Numpy`; that is why the criterion is the
    # import. If this ever became a single label, the criterion would have silently changed.
    assert labels == {"Tensorflow", "Numpy"}, labels
    for i in dataset.included_indices():
        assert not dataset._TF_PATTERN.search(rows[i]["code_context"]), i


def test_the_frozen_table_is_the_recorded_sweep_result():
    """The table must be the sweep's output, not a hand-edited list."""
    if not SWEEP.is_file():
        pytest.skip("full sweep artifact not provisioned")
    sweep = json.loads(SWEEP.read_text())
    assert sweep["included"] == 953 and sweep["done"] == 953
    assert sweep["passed"] == 926
    failed = {int(tid.split("/")[1]) for tid in sweep["failed"]}
    assert len(failed) == 27
    assert failed == set(dataset.measured_exclusions()), (
        "the frozen causes no longer match the rows the sweep failed on")
    for index in failed:
        assert sweep["results"][dataset.task_id(index)]["reward"] == 0
    passed = {int(tid.split("/")[1]) for tid, row in sweep["results"].items()
              if row["reward"] == 1}
    assert passed == set(dataset.included_indices())


@pytest.mark.parametrize("cause", sorted(CAUSE_PATTERNS))
def test_each_recorded_cause_still_reproduces(cause, tmp_path):
    index = dataset.MEASURED_EXCLUSIONS[cause][0]
    reward, evidence = _run_check_directly(index, tmp_path)
    assert reward == 0.0, (cause, reward, evidence)
    assert evidence.get("measured") is True
    text = json.dumps(evidence)
    assert re.search(CAUSE_PATTERNS[cause], text), \
        f"{cause} no longer reports its recorded failure: {text[:600]}"


def test_every_included_row_has_a_verifier_and_an_oracle():
    assert len(dataset.load()[0]) == 926
    assert set(dataset.VERIFY) == {dataset.task_id(i) for i in dataset.included_indices()}
    assert set(dataset.SETUP) == set(dataset.VERIFY)
    assert set(dataset.ENV) == set(dataset.VERIFY)
    # No division: the pool is the released exam-like slice, so every offered row is
    # evaluation material (2026-10-24). `--split default` still gives a run the hash rule.
    assert dataset.SPLIT is None and dataset.SPLIT_SOURCE == "none"
    assert {t["task_id"] for t in dataset.load()[0]} == set(dataset.VERIFY)
    for index in dataset.included_indices()[::97]:
        assert dataset.oracle_files({"task_id": dataset.task_id(index)})["solution.py"].strip()


def test_gold_stays_out_of_setup_and_the_tests_travel_in_verify_inputs():
    index = dataset.included_indices()[0]
    task_id = dataset.task_id(index)
    setup = json.dumps(dataset.SETUP[task_id])
    assert dataset._raw()[index]["reference_code"] not in setup
    # Verify every offered row's complete solver projection, not just its filename.
    tasks, _ = dataset.load()
    for task, row_index in zip(tasks, dataset.included_indices()):
        row = dataset._raw()[row_index]
        files = dataset.SETUP[task['task_id']]['files']
        assert [entry['path'] for entry in files] == ['task.json']
        assert json.loads(files[0]['content']) == {
            'task_id': task['task_id'], 'prompt': row['prompt']}
        assert task['goal'] == row['prompt'] + (
            '\n\nWrite your completed Python solution to solution.py in this directory. '
            'Do not include markdown fences; only the solution body is executed.')
    inputs = {entry["path"]: entry for entry in dataset.VERIFY[task_id]["inputs"]}
    # Pin the official evaluator and each offline CSV, allowing additive platform inputs.
    sources = {
        "verify/check.py": dataset.CHECK_FILE,
        "execution.py": EXECUTION,
        "data/scaffolds/scorer_input.py": ROOT / "data/scaffolds/scorer_input.py",
        **{f"seaborn-data/{name}": dataset.SEABORN_DIR / name
           for name in dataset.SEABORN_CSVS},
    }
    assert sources.keys() | {"context.json", "data/__init__.py",
                            "data/scaffolds/__init__.py"} <= inputs.keys()
    for path, source in sources.items():
        assert inputs[path]["from"] == str(source)
    for path in ("data/__init__.py", "data/scaffolds/__init__.py"):
        assert inputs[path]["content"] == ""
    assert json.loads(inputs["context.json"]["content"]) == {
        "code_context": dataset._raw()[index]["code_context"]}
    for entry in dataset.VERIFY[task_id]["inputs"]:
        if "from" in entry:
            assert Path(entry["from"]).exists(), entry


def test_the_seaborn_rows_are_included_with_their_data_shipped_offline():
    loaders = [i for i, row in enumerate(dataset._raw())
               if "load_dataset(" in row["code_context"]]
    assert len(loaders) == 14
    assert not (set(loaders) & dataset.excluded_indices())
    assert {p.name for p in dataset.SEABORN_DIR.glob("*.csv")} == set(dataset.SEABORN_CSVS)
    for index in loaders:
        assert dataset.task_id(index) in dataset.VERIFY


def test_the_construction_is_the_official_one_for_representative_rows():
    for index in dataset.included_indices()[::131]:
        row = dataset._raw()[index]
        program = official_program(row, row["reference_code"])
        assert "test_execution(code)" in program
        assert row["code_context"] in program
        assert ("test_string(code)" in program) == ("test_string(" in row["code_context"])


@pytest.mark.parametrize("index", [0, 519, 539])
def test_included_reference_code_reaches_one(index):
    """519 and 539 pass only because the seaborn data is provisioned offline."""
    if index not in dataset.included_indices():
        pytest.skip(f"ds1000/{index} is excluded on this host")
    proc = _control(index, "--oracle")
    assert proc.returncode == 0, proc.stdout[-600:] + proc.stderr[-400:]
    assert "reward  1" in proc.stdout, proc.stdout[-600:]


@pytest.mark.parametrize("index", [0, 519])
def test_no_answer_reaches_zero(index):
    proc = _control(index, "--noop")
    assert proc.returncode == 0, proc.stdout[-600:]
    assert "reward  0" in proc.stdout, proc.stdout[-600:]


def test_an_excluded_row_is_not_a_task():
    offered = {t["task_id"] for t in dataset.load()[0]}
    for index in sorted(dataset.excluded_indices())[:5]:
        assert dataset.task_id(index) not in dataset.VERIFY
        assert dataset.task_id(index) not in offered
