"""The 100-per-fold ScienceWorld study setting, and the pool switch that selects it.

Three things are being pinned here, and each has a failure mode that would be invisible in a
curve:

1. **the sample is what it says it is** -- 100 tasks per official fold, all 29 task types in
   every fold, no task id shared between folds;
2. **it is a property of the repository, not of the machine** -- rebuilding it from the
   benchmark's archive reproduces the committed file byte for byte (skipped when the archive
   is absent, like the fold-drift test next door, because a machine without the archive cannot
   check a transcription of it);
3. **the switch changes only what it claims** -- `HG_SCIENCEWORLD_POOL=dev|test` swaps the
   eval fold while the train side stays the same 100 tasks, and with the switch unset the
   dataset is exactly what it was (the whole test fold, hash-split).

The env-dependent cases run in a subprocess on purpose: the adapter reads the variable at
import time, so re-importing inside one process would test a cached module rather than the
switch.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ASSETS = ROOT / "data" / "datasets" / "scienceworld"
STUDY = ASSETS / "study_300.json"
ARCHIVE_CANDIDATES = (
    pathlib.Path("/tmp/goldpaths-all.zip"),
    pathlib.Path("/tmp/scw/ScienceWorld-main/goldpaths/goldpaths-all.zip"),
)
FOLDS = ("train", "dev", "test")
TYPES_WITH_GOLD = 29


@pytest.fixture(scope="module")
def payload() -> dict:
    assert STUDY.is_file(), f"no study setting at {STUDY}"
    return json.loads(STUDY.read_text())


def ids(payload: dict, fold: str) -> list[str]:
    return [f"{e['task_name']}/{e['variation']}" for e in payload["pools"][fold]]


def test_every_fold_has_one_hundred_tasks_and_all_task_types(payload):
    for fold in FOLDS:
        rows = payload["pools"][fold]
        assert len(rows) == 100, (fold, len(rows))
        assert len({r["task_name"] for r in rows}) == TYPES_WITH_GOLD, fold
        assert len(set(ids(payload, fold))) == 100, fold
        assert all(isinstance(r["gold_moves"], int) and r["gold_moves"] > 0 for r in rows)
        assert rows == sorted(rows, key=lambda r: (r["task_name"], r["variation"])), fold


def test_folds_share_no_task(payload):
    seen = {fold: set(ids(payload, fold)) for fold in FOLDS}
    for a, b in (("train", "dev"), ("train", "test"), ("dev", "test")):
        assert not (seen[a] & seen[b]), (a, b, sorted(seen[a] & seen[b])[:3])


def test_setting_records_the_archive_it_came_from(payload):
    archive = payload["archive"]
    assert archive["folds"] == {"train": 3442, "dev": 1721, "test": 1744}
    assert len(archive["sha256"]) == 64
    assert payload["setting"] == {"seed": 20261021, "per_fold": 100, "step_budget": 100,
                                  "folds": list(FOLDS)}


def test_rebuild_from_the_archive_reproduces_the_file(payload):
    archive = next((p for p in ARCHIVE_CANDIDATES if p.is_file()), None)
    if archive is None:
        pytest.skip("no goldpaths archive on this machine to rebuild from")
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import scienceworld_study_sample as tool
    finally:
        sys.path.pop(0)
    rebuilt = tool.build(archive)
    assert rebuilt == payload


def _adapter(env: dict[str, str]) -> dict:
    """SPLIT/OFFICIAL_EVAL/task counts as a fresh process sees them under `env`."""
    script = (
        "import json, importlib\n"
        "m = importlib.import_module('data.datasets.scienceworld')\n"
        "tasks, _ = m.load()\n"
        "split = None if m.SPLIT is None else {k: len(v) for k, v in m.SPLIT.items()}\n"
        "print(json.dumps({'split': split, 'source': m.SPLIT_SOURCE,\n"
        "                  'train_ids': (m.SPLIT or {}).get('train', []),\n"
        "                  'eval_ids': (m.SPLIT or {}).get('eval', []),\n"
        "                  'official': list(m.OFFICIAL_EVAL) if m.OFFICIAL_EVAL else None,\n"
        "                  'tasks': len(tasks), 'env': len(m.ENV), 'verify': len(m.VERIFY)}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), capture_output=True,
                          text=True, env={**__import__("os").environ, **env})
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_without_the_switch_the_whole_official_test_fold_is_the_pool():
    """No study setting: no division, and the pool is the official `test` fold.

    It used to be hash-split (1175 train / 569 eval) over that same fold, which called a
    third of the benchmark's held-out material study material; removed 2026-10-24.
    """
    got = _adapter({})
    assert got["split"] is None and got["source"] == "none"
    assert got["official"] is None
    assert got["tasks"] == 1744


@pytest.mark.parametrize("pool", ["dev", "test"])
def test_study_switch_scores_one_hundred_of_that_fold(payload, pool):
    got = _adapter({"HG_SCIENCEWORLD_POOL": pool})
    assert got["split"] == {"train": 100, "eval": 100}
    assert got["tasks"] == 200 and got["env"] == 200 and got["verify"] == 200
    # Order is the file's order -- sorted by (task_name, variation) as a tuple, which is *not*
    # the same as sorting the joined "name/variation" string: `-` sorts before `/`, so
    # `task-2-power-component-(...)` precedes `task-2-power-component/…`.
    assert got["eval_ids"] == ids(payload, pool)
    assert got["official"] == ids(payload, pool)
    assert got["train_ids"] == ids(payload, "train")
    assert set(got["eval_ids"]).isdisjoint(got["train_ids"])


def test_the_two_eval_folds_are_different_and_both_backed_by_the_same_train_side(payload):
    dev = _adapter({"HG_SCIENCEWORLD_POOL": "dev"})
    test = _adapter({"HG_SCIENCEWORLD_POOL": "test"})
    assert dev["train_ids"] == test["train_ids"]
    assert set(dev["eval_ids"]).isdisjoint(test["eval_ids"])


def test_an_unknown_pool_is_refused():
    proc = subprocess.run([sys.executable, "-c",
                           "import data.datasets.scienceworld"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          env={**__import__("os").environ, "HG_SCIENCEWORLD_POOL": "train"})
    assert proc.returncode != 0
    assert "HG_SCIENCEWORLD_POOL" in proc.stderr
