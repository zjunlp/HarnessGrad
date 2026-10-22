"""Whose train/eval division a run uses, and why it has to be recorded.

`data/scaffolds/registry.py` distinguishes two things that used to be one:

* a **declaration** (`SPLIT` in the adapter) is a claim about the benchmark -- "this dataset
  has a study side and an exam side" -- so both sides must be filled;
* a **run-time division** (`--split default|official|<file>`) is a choice about one
  experiment, so only the side the run evaluates has to be filled. `--side eval --split
  official` is the report run: the benchmark's whole evaluation set, with no study material
  in the pool at all.

The division is a property of the protocol, not of the benchmark, so the run may supply it.
What it may not do is leave the choice unrecorded: `split_source` and `split_sha` go on every
run, and two curve points belong on one axis only when the sha agrees.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.scaffolds.registry as registry  # noqa: E402


def _tasks() -> list[dict]:
    return [{"task_id": f"probe/{i}", "goal": "g", "inputs": []} for i in range(30)]


# ------------------------------------------------------------- the two kinds ---


def test_a_declaration_still_needs_both_sides():
    """A dataset that declares one empty side has a bug, and saying so is the point.

    The split covers every task in both arms, so the only thing wrong with each is the
    empty side -- otherwise the coverage rule fires first and this test would pass for
    the wrong reason.
    """
    tasks = _tasks()
    ids = [t["task_id"] for t in tasks]
    with pytest.raises(ValueError, match="empty side"):
        registry.validate_split("probe", tasks,
                                {"train": ids, "eval": []})
    with pytest.raises(ValueError, match="empty side"):
        registry.validate_split("probe", tasks,
                                {"train": [], "eval": ids})


def test_a_run_time_division_needs_only_the_side_it_evaluates():
    """`--side eval --split official`: the official set, no study material in the pool."""
    tasks = _tasks()
    ids = [t["task_id"] for t in tasks]
    split = registry.validate_split("probe", tasks,
                                    {"train": [], "eval": ids},
                                    require_nonempty=("eval",))
    assert len(split["eval"]) == len(tasks) and split["train"] == []
    with pytest.raises(ValueError, match="empty side"):
        registry.validate_split("probe", tasks, {"train": [], "eval": ids},
                                require_nonempty=("train",))


# --------------------------------------------------------------- the rules ---


def test_the_default_rule_is_the_one_the_adapters_used_to_write_themselves():
    tasks = _tasks()
    split = registry.default_split(tasks)
    assert set(split["train"]) | set(split["eval"]) == {t["task_id"] for t in tasks}
    assert not (set(split["train"]) & set(split["eval"]))
    for task in tasks:
        want = registry.is_eval_id(task["task_id"])
        assert (task["task_id"] in split["eval"]) is want


def test_the_default_rule_is_stable_when_tasks_are_added_or_removed():
    """The property the rule exists for: a split that moves cannot be compared with itself."""
    before = registry.default_split([{"task_id": f"probe/{i}"} for i in range(30)])
    after = registry.default_split([{"task_id": f"probe/{i}"} for i in range(31)])
    for task_id in before["eval"]:
        assert task_id in after["eval"]
    for task_id in before["train"]:
        assert task_id in after["train"]


def test_official_refuses_a_dataset_that_has_not_declared_it():
    """Absent is "has not said", which is refused; it is not the same as `None`.

    `demo` is the honest case: it is a fixture (`KIND = "fixture"`), the platform's own
    scaffolding rather than a benchmark, so it has no official evaluation set to state.
    """
    with pytest.raises(ValueError, match="OFFICIAL_EVAL"):
        registry.official_split("demo", [{"task_id": "demo/0"}])


def test_no_refused_dataset_pretends_to_have_an_evaluation_set():
    """A dataset that cannot be scored must not offer `--split official`.

    Derived from the adapters rather than listed here: whatever sets `REFUSED` is in this
    set, so a new refusal is covered the moment it is written. The number of refusals
    changes as adapters gain scoring support; only the per-adapter contract is fixed.
    """
    refused = []
    for name in registry.published():
        module = importlib.import_module(f"data.datasets.{name}")
        if getattr(module, "REFUSED", None):
            refused.append(name)
            assert not hasattr(module, registry.OFFICIAL_EVAL_ATTR), name
            with pytest.raises(ValueError, match="OFFICIAL_EVAL"):
                registry.official_split(name, [{"task_id": f"{name}/0"}])
    assert refused, "the published catalog has no refused adapters to check"


def test_every_runnable_dataset_answers_the_official_question():
    """The complement: a dataset without `REFUSED` has to have an answer, even if it is `None`.

    `hasattr` and not "is not None" is the assertion -- `None` is a real answer ("no study
    material in the pool"), and the failure this catches is silence.

    **One state is neither an answer nor silence: the payload is not here.** `costbench`,
    `personamem_v2` and `strongreject` read their official set out of a prepared payload,
    so on a machine where `--fetch` has not run they cannot answer it -- and must not
    answer it wrongly. The requirement for that state is that `load()` fails *and names the
    root it is missing*, which is what separates "not provisioned" from "declined the
    question". This test used to fail on the first of the three with a raw
    `FileNotFoundError` out of the manifest read, which is how the missing absolute
    default (a `parents[3]` path that resolved above the repository) was found.
    """
    published = list(registry.published())
    answered, refused, unprovisioned = [], [], []
    for name in published:
        module = importlib.import_module(f"data.datasets.{name}")
        if getattr(module, "REFUSED", None):
            refused.append(name)
        elif hasattr(module, registry.OFFICIAL_EVAL_ATTR):
            answered.append(name)
        else:
            unprovisioned.append(name)
        if name in unprovisioned:
            with pytest.raises((ValueError, OSError)) as caught:
                module.load()
            root = getattr(module, "DATASET_ROOT", None)
            assert root is not None and str(root) in str(caught.value), (
                f"{name} cannot answer OFFICIAL_EVAL and its load() error does not name "
                f"the root to provision: {caught.value}")
    assert len(answered) + len(refused) + len(unprovisioned) == len(published)
    assert unprovisioned or answered, "no dataset answered and none was unprovisioned"
    for name in refused + unprovisioned:
        assert name in published


def test_official_none_means_the_pool_holds_no_study_material():
    """`None` is a real answer, not silence: the pool *is* the scored split.

    `strategyqa` is the case -- the released table is its test split, nothing else is
    loaded, so every task in the pool belongs to the benchmark's evaluation set.
    """
    tasks = [{"task_id": f"strategyqa/test/{i}"} for i in range(10)]
    split = registry.official_split("strategyqa", tasks)
    assert len(split["eval"]) == 10 and split["train"] == []


def test_supplied_divisions_are_validated_like_any_other():
    tasks = _tasks()
    ids = [t["task_id"] for t in tasks]
    with pytest.raises(ValueError, match="unknown task"):
        registry.validate_split("probe", tasks, {"train": ["probe/nope"], "eval": []})
    with pytest.raises(ValueError, match="both sides"):
        registry.validate_split("probe", tasks, {"train": ids, "eval": [ids[0]]})
    with pytest.raises(ValueError, match="neither side"):
        registry.validate_split("probe", tasks, {"train": ids[:5], "eval": ids[5:-1]})
    with pytest.raises(ValueError, match="unknown side"):
        registry.validate_split("probe", tasks, {"train": ids, "holdout": []})


# ------------------------------------------------------------- the record ---


def test_the_sha_identifies_the_task_sets_and_not_their_order():
    a = {"train": ["x", "y"], "eval": ["z"]}
    b = {"train": ["y", "x"], "eval": ["z"]}
    c = {"train": ["x", "y"], "eval": ["w"]}
    assert registry.split_sha(a) == registry.split_sha(b)
    assert registry.split_sha(a) != registry.split_sha(c)


def test_a_division_is_declared_or_absent_and_never_invented():
    """Two states, and the sha has to tell them apart within each.

    * a benchmark that publishes **both** sides (`gsm8k`, whose study side is the whole
      official train split, and `triviaqa`, whose study side is a declared sample of it):
      the adapter declares the division, so `declared` == `official` and `default` (the
      platform's hash third) is a *different* task split;
    * a benchmark whose table is **one** scored split (`aime`, `musique`, `strategyqa`,
      `chessbench_puzzles`, ...): the adapter declares **no** division at all --
      `load_split` returns `None`, `official` is the whole pool, and a `--split default`
      run is the only way to get a division, which is why the sha differs from `official`.

    The second state used to be filled with `default_split`, so `declared == default` and
    a benchmark's dev or test file was silently cut in two. The operator removed that
    invented division on 2026-10-24; this test is what would notice it coming back.
    """
    for name in ("gsm8k", "triviaqa"):
        tasks, _, declared = registry.load_split(name)
        official = registry.official_split(name, tasks)
        assert declared is not None, f"{name} publishes both sides"
        assert registry.split_sha(declared) == registry.split_sha(official), name
        assert registry.split_sha(declared) != registry.split_sha(
            registry.default_split(tasks)), name
        assert official["train"], f"{name} studies a train side"
        assert official["eval"] == declared["eval"], name

    for name in ("aime", "musique", "strategyqa", "chessbench_puzzles"):
        tasks, _, declared = registry.load_split(name)
        official = registry.official_split(name, tasks)
        assert declared is None, f"{name} loads one scored split; it declares no division"
        assert official["train"] == [], name
        assert official["eval"] == [t["task_id"] for t in tasks], name
        assert registry.split_sha(official) != registry.split_sha(
            registry.default_split(tasks)), name


def test_the_driver_exposes_all_three_modes_and_a_file(tmp_path):
    import subprocess
    proc = subprocess.run([sys.executable, "driver.py", "--help"], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0
    for token in ("--split", "declared", "default", "official", "split_sha"):
        assert token in proc.stdout, token
