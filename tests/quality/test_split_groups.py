"""Opt-in grouping protects related tasks without changing ungrouped datasets."""
import copy
import sys
from types import ModuleType

import pytest

from data.scaffolds import registry


@pytest.fixture
def grouped(monkeypatch):
    name = "_split_group_fixture"
    tasks = [{"task_id": f"probe/{group}/{variant}", "goal": "g", "inputs": []}
             for group in ("a", "b") for variant in range(3)]
    # Eval and train group ids respectively, regardless of each variant's task-id hash.
    groups = {task["task_id"]: ("00" + "a" * 62 if "/a/" in task["task_id"]
                               else "01" + "b" * 62) for task in tasks}
    split = {"train": [task["task_id"] for task in tasks[3:]],
             "eval": [task["task_id"] for task in tasks[:3]]}
    module = ModuleType(registry.module_name(name))
    module.load = lambda: (tasks, {task["task_id"]: "metric" for task in tasks})
    module.SPLIT_GROUPS = groups
    module.SPLIT = split
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return name, tasks, groups, split, module


def test_default_uses_the_group_digest_once_and_preserves_task_order(grouped):
    name, tasks, groups, split, _ = grouped
    before = copy.deepcopy(tasks)
    assert registry.load_split_groups(name, tasks) == groups
    assert registry.default_split(tasks, split_groups=groups) == split
    assert registry.validate_split(name, tasks, split, split_groups=groups) == split
    assert tasks == before


def test_group_membership_is_stable_when_variants_are_added_or_reordered(grouped):
    _, tasks, groups, split, _ = grouped
    added = "probe/a/new-variant"
    changed = list(reversed(tasks)) + [{"task_id": added}]
    result = registry.default_split(changed, split_groups={**groups, added: groups[split["eval"][0]]})
    assert set(result["train"]) == set(split["train"])
    assert set(result["eval"]) == set(split["eval"]) | {added}


def test_declared_splits_cannot_scatter_a_group(grouped):
    name, tasks, _, split, module = grouped
    assert registry.load_split(name)[2] == split
    moved = split["eval"][0]
    module.SPLIT = {"train": split["train"] + [moved], "eval": split["eval"][1:]}
    with pytest.raises(ValueError, match="related tasks must stay together"):
        registry.load_split(name)


def test_ungrouped_adapters_keep_task_id_hashing_and_supplied_splits(grouped, monkeypatch):
    name, tasks, _, _, module = grouped
    monkeypatch.delattr(module, "SPLIT_GROUPS")
    assert registry.load_split_groups(name, tasks) is None
    expected = {"train": [task["task_id"] for task in tasks
                          if not registry.is_eval_id(task["task_id"])],
                "eval": [task["task_id"] for task in tasks
                         if registry.is_eval_id(task["task_id"])]}
    assert registry.default_split(tasks) == expected
    assert registry.default_split(tasks, split_groups=None) == expected
    # Similar task names alone do not opt a dataset into a grouping constraint.
    scattered = {"train": [task["task_id"] for task in tasks[1:]],
                 "eval": [tasks[0]["task_id"]]}
    assert registry.validate_split(name, tasks, scattered) == scattered
    module.SPLIT = scattered
    assert registry.load_split(name)[2] == scattered


@pytest.mark.parametrize("invalid", ["missing", "unknown", "not-dict", "empty-id",
                                     "uppercase-id", "unhashed-key", "non-string-id"])
def test_invalid_group_declarations_are_refused_by_loading_and_split_apis(grouped, invalid):
    name, tasks, groups, split, module = grouped
    damaged = dict(groups)
    tid = tasks[0]["task_id"]
    if invalid == "missing":
        damaged.pop(tid)
    elif invalid == "unknown":
        damaged["unknown/task"] = groups[tid]
    elif invalid == "not-dict":
        damaged = list(groups.values())
    else:
        damaged[tid] = {"empty-id": "", "uppercase-id": "A" * 64,
                        "unhashed-key": "original request", "non-string-id": 123}[invalid]
    module.SPLIT_GROUPS = damaged
    with pytest.raises(ValueError, match="SPLIT_GROUPS"):
        registry.load_split_groups(name, tasks)
    with pytest.raises(ValueError, match="SPLIT_GROUPS"):
        registry.default_split(tasks, split_groups=damaged)
    with pytest.raises(ValueError, match="SPLIT_GROUPS"):
        registry.validate_split(name, tasks, split, split_groups=damaged)


def test_grouping_alone_does_not_declare_a_split(grouped):
    name, tasks, groups, _, module = grouped
    module.SPLIT = None
    assert registry.load_split(name)[2] is None
    assert registry.load_split_groups(name, tasks) == groups


def test_group_checks_allow_an_official_pool_with_no_study_material(grouped):
    name, tasks, groups, _, module = grouped
    module.OFFICIAL_EVAL = None
    official = registry.official_split(name, tasks)
    assert registry.validate_split(name, tasks, official,
                                   require_nonempty=("eval",), split_groups=groups) == official
    assert official == {"train": [], "eval": [task["task_id"] for task in tasks]}
