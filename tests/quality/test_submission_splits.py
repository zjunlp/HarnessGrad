"""Console readiness must select the same tasks and reject the same unsafe splits as CLI."""
import json

import pytest

from console import submissions
from data.scaffolds import registry
from test_split_groups import grouped
from test_strongreject_dataset import prepared


@pytest.fixture
def metadata_selection(monkeypatch):
    selected = []
    load_envs = registry.load_envs
    def record_envs(name, *, task_ids=None):
        selected.extend(task_ids)
        return load_envs(name, task_ids=task_ids)
    monkeypatch.setattr(registry, "load_envs", record_envs)
    return selected


def _research_split(prepared):
    tasks, _ = prepared.load()
    groups = registry.load_split_groups("strongreject", tasks)
    return registry.default_split(tasks, split_groups=groups)


@pytest.mark.parametrize("mode,side", [("declared", "train"), ("declared", "eval"),
                                       ("official", "eval")])
def test_strongreject_preflight_without_a_research_split_uses_all_tasks(
        prepared, metadata_selection, mode, side):
    info = submissions._metadata({"dataset": "strongreject", "split": mode, "side": side})
    assert metadata_selection == list(prepared.rows())
    assert info == {"tasks": 150, "env_kinds": ["files"], "images": []}


@pytest.mark.parametrize("side", ["train", "eval"])
def test_strongreject_preflight_accepts_grouped_json_membership(
        prepared, tmp_path, monkeypatch, metadata_selection, side):
    split = _research_split(prepared)
    (tmp_path/"grouped.json").write_text(json.dumps(split))
    monkeypatch.setattr(submissions, "PLATFORM_ROOT", tmp_path)
    info = submissions._metadata({"dataset": "strongreject", "split": "grouped.json", "side": side})
    assert metadata_selection == split[side]
    assert info["tasks"] == len(split[side])


def test_strongreject_preflight_rejects_scattered_json_before_reading_environments(
        prepared, tmp_path, monkeypatch):
    tasks, _ = prepared.load()
    (tmp_path/"unsafe.json").write_text(json.dumps(registry.default_split(tasks)))
    monkeypatch.setattr(submissions, "PLATFORM_ROOT", tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("environments were read before the grouped split was checked")
    monkeypatch.setattr(registry, "load_envs", forbidden)
    with pytest.raises(ValueError, match="related tasks must stay together"):
        submissions._metadata({"dataset": "strongreject", "split": "unsafe.json", "side": "eval"})


def test_preflight_also_checks_groups_when_an_official_boundary_is_inconsistent(
        grouped, monkeypatch):
    name, _, _, split, module = grouped
    module.OFFICIAL_EVAL = split["eval"][:1]
    def forbidden(*args, **kwargs):
        pytest.fail("environments were read for a split that scatters a group")
    monkeypatch.setattr(registry, "load_envs", forbidden)
    with pytest.raises(ValueError, match="related tasks must stay together"):
        submissions._metadata({"dataset": name, "split": "official", "side": "eval"})


@pytest.mark.parametrize("mode", ["declared", "default", "file"])
@pytest.mark.parametrize("side", ["train", "eval"])
def test_ungrouped_demo_retains_membership_for_every_split_source(
        tmp_path, monkeypatch, metadata_selection, mode, side):
    tasks, _, declared = registry.load_split("demo")
    assert registry.load_split_groups("demo", tasks) is None
    if mode == "declared":
        expected, source = declared, mode
    elif mode == "default":
        expected, source = registry.default_split(tasks), mode
    else:
        ids = [task["task_id"] for task in tasks]
        expected = {"train": ids[:2], "eval": ids[2:]}
        source = "split.json"
        (tmp_path/source).write_text(json.dumps(expected))
        monkeypatch.setattr(submissions, "PLATFORM_ROOT", tmp_path)
    info = submissions._metadata({"dataset": "demo", "split": source, "side": side})
    assert metadata_selection == expected[side]
    assert info == {"tasks": len(expected[side]), "env_kinds": ["files"], "images": []}
