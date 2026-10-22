"""tau-bench's split is the benchmark's own, file for file, and this file pins it.

Why this test exists: the adapter used to *impose* a division -- retail to study, airline
to be scored -- over two test files only. That is not the benchmark's split: retail ships
`tasks_train.TASKS_TRAIN` (500) / `tasks_dev.TASKS_DEV` (20) / `tasks_test.TASKS_TEST`
(115), and airline ships one test file (50). The imposed division also dropped every
retail test task from the exam, so a tau-bench number here was a score on airline's test
file alone.

The claim being tested is that the sides are the official ones, so the expected sets come
from `PROVENANCE.json` (which names the four upstream task files and their counts) and
from the staged ground-truth records' own `domain`/`split` fields -- not from the
adapter's pool, which would make the test agree with whatever the adapter did.

**Why reload and not a subprocess.** `HG_TAU_BENCH_POOL` is read at import, so each case
sets its environment with `monkeypatch` and re-imports; an autouse fixture puts the module
back on the default pool afterwards, because another test file imports it too.
"""
from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.datasets.tau_bench as tau_bench  # noqa: E402
from data.scaffolds import registry  # noqa: E402

POOL_KNOB = "HG_TAU_BENCH_POOL"

#: The four official task files and their counts, as `PROVENANCE.json` records them.
OFFICIAL_SETS = {"retail/train": 500, "retail/dev": 20, "retail/test": 115, "airline/test": 50}


@pytest.fixture(scope="module")
def records() -> dict[str, dict]:
    """Every staged ground-truth record, by task id."""
    return {record["task_id"]: record for record in tau_bench._ALL_GT}


@pytest.fixture(autouse=True)
def _back_to_the_default_pool(monkeypatch):
    yield
    monkeypatch.delenv(POOL_KNOB, raising=False)
    importlib.reload(tau_bench)


def _reload(monkeypatch, pool: str | None = None):
    monkeypatch.delenv(POOL_KNOB, raising=False)
    if pool is not None:
        monkeypatch.setenv(POOL_KNOB, pool)
    importlib.reload(tau_bench)
    return tau_bench


def _returned_ids(module, domain: str, split: str) -> list[str]:
    """The ids `load()` returns for one official set, in the adapter's order."""
    return [record["task_id"] for record in module._GT
            if (record["domain"], record["split"]) == (domain, split)]


def test_the_four_official_task_sets_are_the_ones_that_were_staged():
    """The staged assets are the benchmark's four files, not a subset of two of them."""
    provenance = json.loads((tau_bench.ASSETS / "PROVENANCE.json").read_text())
    assert provenance["task_counts"] == OFFICIAL_SETS
    assert provenance["task_files"] == {
        "retail/train": "tau_bench/envs/retail/tasks_train.py",
        "retail/dev": "tau_bench/envs/retail/tasks_dev.py",
        "retail/test": "tau_bench/envs/retail/tasks_test.py",
        "airline/test": "tau_bench/envs/airline/tasks_test.py",
    }
    # The legacy `tasks.py` set (185 retail / 76 airline) is not what the environment
    # scored and is not staged.
    assert "tasks.py" not in " ".join(provenance["task_files"].values())
    assert len(tau_bench._ALL_GT) == sum(OFFICIAL_SETS.values()) == 685


def test_every_task_id_names_the_file_it_came_from(records):
    """`<domain>_<split>_<index>`: the three retail files each index from 0."""
    for task_id, record in records.items():
        assert task_id == f"{record['domain']}_{record['split']}_{record['index']:03d}"
        assert record["split"] in ("train", "dev", "test")
        path = tau_bench.ASSETS / "gt" / f"{task_id}.json"
        assert path.is_file()
    per_set = Counter(f"{r['domain']}/{r['split']}" for r in records.values())
    assert dict(per_set) == OFFICIAL_SETS


def test_the_default_pool_studies_train_and_scores_both_official_test_files(monkeypatch):
    """The two-way split, in the benchmark's terms: retail train against retail+airline test."""
    module = _reload(monkeypatch)
    assert module._LOAD_ERROR is None
    assert module.SPLIT_SOURCE == "declared"
    assert module.SPLIT["train"] == _returned_ids(module, "retail", "train")
    assert set(module.SPLIT["eval"]) == (set(_returned_ids(module, "retail", "test"))
                                        | set(_returned_ids(module, "airline", "test")))
    assert (len(module.SPLIT["train"]), len(module.SPLIT["eval"])) == (495, 139)
    assert module.OFFICIAL_EVAL == module.SPLIT["eval"]
    # Every third retail task file is study material, so it is not in the exam, and the
    # airline file is entirely an exam.
    assert not (set(module.SPLIT["train"]) & set(module.SPLIT["eval"]))
    assert all(task_id.startswith("airline_test_")
               for task_id in _returned_ids(module, "airline", "test"))
    tasks, _ = module.load()
    assert {task["task_id"] for task in tasks} == (
        set(module.SPLIT["train"]) | set(module.SPLIT["eval"]))


def test_the_declared_split_passes_the_registry_check(monkeypatch):
    """`validate_split` requires the sides to be disjoint and to cover every returned task."""
    module = _reload(monkeypatch)
    tasks, _, split = registry.load_split("tau_bench")
    assert split == module.SPLIT
    assert set(split["train"]) | set(split["eval"]) == {t["task_id"] for t in tasks}


def test_pool_dev_scores_the_official_dev_split_and_never_studies_it(monkeypatch):
    """The selection protocol: train is studied, dev is scored, the exam is not loaded."""
    module = _reload(monkeypatch, "dev")
    assert module._LOAD_ERROR is None
    assert module.SPLIT["train"] == _returned_ids(module, "retail", "train")
    assert module.SPLIT["eval"] == _returned_ids(module, "retail", "dev")
    assert len(module.SPLIT["eval"]) == 19
    tasks, _ = module.load()
    loaded = {task["task_id"] for task in tasks}
    assert loaded == set(module.SPLIT["train"]) | set(module.SPLIT["eval"])
    # No test task is loaded at all: the exam stays unmeasured.
    assert not any(task_id.split("_")[1] == "test" for task_id in loaded)
    # dev is an official split but not the set the published numbers are computed on, so
    # `--side eval --split official` has an empty eval side and is refused, not invented.
    assert module.OFFICIAL_EVAL == []


def test_the_train_pool_is_refused(monkeypatch):
    """A score on the study material is not an evaluation."""
    module = _reload(monkeypatch, "train")
    with pytest.raises(ValueError, match="not an official split") as caught:
        module.load()
    assert "not `train`" in str(caught.value)


def test_a_misspelled_pool_names_the_pools_that_exist(monkeypatch):
    """A typo must name itself rather than fall back to the default pool."""
    module = _reload(monkeypatch, "tests")
    with pytest.raises(ValueError, match="not an official split") as caught:
        module.load()
    for pool in module.POOLS:
        assert pool in str(caught.value)


def test_excluded_tasks_are_absent_from_both_sides(records, monkeypatch):
    """A non-discriminating task is removed from `load()`, not just from a side."""
    module = _reload(monkeypatch)
    excluded = set(module.EXCLUDED_TASKS)
    assert len(excluded) == 32
    assert excluded <= set(records)
    # The exclusion is measured per task, so it can be counted per official set.
    assert Counter(f"{records[tid]['domain']}/{records[tid]['split']}"
                   for tid in excluded) == Counter({
                       "retail/train": 5, "retail/dev": 1, "retail/test": 7,
                       "airline/test": 19})
    loaded = {task["task_id"] for task in module.load()[0]}
    assert not (loaded & excluded)
    assert not (set(module.SPLIT["train"]) & excluded)
    assert not (set(module.SPLIT["eval"]) & excluded)
