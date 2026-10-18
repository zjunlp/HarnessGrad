"""Selected declarations are resolved only after the driver's task selection."""
from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
from types import ModuleType

import pytest
from data.scaffolds import registry


class SelectedOnly(Mapping):
    def __init__(self, keys, selected, value):
        self._keys = list(keys)
        self.selected = selected
        self.value = value
        self.reads = []

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __getitem__(self, key):
        if key not in self._keys:
            raise KeyError(key)
        self.reads.append(key)
        if key != self.selected:
            raise RuntimeError(f"unselected runtime/model unavailable: {key}")
        return self.value


@pytest.fixture
def lazy_dataset(monkeypatch):
    module = ModuleType("data.demo")
    module.KIND = "fixture"
    module.ORACLE_SOURCE = "released"
    tasks = [{"task_id": tid, "goal": "Write ok to answer.txt."}
             for tid in ("chosen", "unprepared-train", "unprepared-eval")]
    scorable = {t["task_id"]: "ok" for t in tasks}
    module.load = lambda: (tasks, scorable)
    module.SPLIT = {"train": ["chosen", "unprepared-train"], "eval": ["unprepared-eval"]}
    keys = list(scorable)
    module.SETUP = SelectedOnly(keys, "chosen", {"files": []})
    module.VERIFY = SelectedOnly(keys, "chosen", {"kind": "answer", "expected": "ok"})
    module.ENV = SelectedOnly(keys, "chosen", {"kind": "files"})
    monkeypatch.setitem(sys.modules, "data.demo", module)
    return module


def test_explicit_selection_never_reads_unselected_values(lazy_dataset):
    tasks, setups, verifiers = registry.load_tasks("demo", task_ids=["chosen"])
    envs = registry.load_envs("demo", task_ids=["chosen"])
    assert [t["task_id"] for t in tasks] == ["chosen"]
    for table in (setups, verifiers, envs):
        assert type(table) is dict and set(table) == {"chosen"}
    for name in ("SETUP", "VERIFY", "ENV"):
        assert getattr(lazy_dataset, name).reads == ["chosen"]


@pytest.mark.parametrize("loader", [registry.load_tasks, registry.load_envs])
def test_default_still_resolves_every_task_and_rejects_unavailable(loader, lazy_dataset):
    with pytest.raises(RuntimeError, match="unselected runtime/model unavailable"):
        loader("demo")


@pytest.mark.parametrize("loader", [registry.load_tasks, registry.load_envs])
@pytest.mark.parametrize("ids,reason", [(["missing"], "unknown"), (["chosen", "chosen"], "duplicate"),
                                        ("chosen", "iterable"), ([None], "strings")])
def test_invalid_selection_fails_before_any_declaration_read(loader, lazy_dataset, ids, reason):
    with pytest.raises(ValueError, match=reason):
        loader("demo", task_ids=ids)
    assert not any(getattr(lazy_dataset, name).reads for name in ("SETUP", "VERIFY", "ENV"))


def test_empty_selection_reads_no_declaration_values(lazy_dataset):
    assert registry.load_tasks("demo", task_ids=[]) == ([], {}, {})
    assert registry.load_envs("demo", task_ids=[]) == {}
    assert not any(getattr(lazy_dataset, name).reads for name in ("SETUP", "VERIFY", "ENV"))


@pytest.mark.parametrize("name,loader", [("SETUP", registry.load_tasks), ("VERIFY", registry.load_tasks),
                                         ("ENV", registry.load_envs)])
def test_unselected_unknown_declaration_keys_are_still_rejected(lazy_dataset, name, loader):
    getattr(lazy_dataset, name)._keys.append("nonexistent")
    with pytest.raises(ValueError, match="unknown"):
        loader("demo", task_ids=["chosen"])


def test_legacy_answer_and_default_environment_for_selected_tasks(lazy_dataset):
    del lazy_dataset.VERIFY
    del lazy_dataset.ENV
    tasks, setups, verifiers = registry.load_tasks("demo", task_ids=(tid for tid in ["chosen"]))
    assert verifiers == {"chosen": {"kind": "answer", "expected": "ok"}}
    assert registry.load_envs("demo", task_ids=["chosen"])["chosen"]["kind"] == "files"


def test_driver_runs_one_real_sandbox_task_before_resolving_other_declarations(lazy_dataset, tmp_path, monkeypatch):
    import driver
    source = tmp_path / "source"
    source.mkdir()
    (source / "harness.json").write_text(json.dumps({"name": "selected-control", "version": "1", "entrypoint": "agent.py"}))
    (source / "agent.py").write_text("import argparse\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()\nPath(a.workdir,'answer.txt').write_text('ok')\n")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(driver, "load_env", lambda *_: {})
    monkeypatch.setattr(driver, "_probe_tool_calls", lambda *a, **k: pytest.fail("model calls are forbidden"))
    monkeypatch.setattr(sys, "argv", ["driver.py", "--harness", str(source), "--dataset", "demo",
        "--sampling", "all", "--side", "train", "--tasks", "chosen", "--rounds", "0",
        "--improver", "none", "--run-id", "selected-control", "--runs-root", str(tmp_path / "runs"),
        "--work-root", str(tmp_path / "work")])
    assert driver.main() == 0
    for name in ("SETUP", "VERIFY", "ENV"):
        assert getattr(lazy_dataset, name).reads == ["chosen"]
    curve = tmp_path / "runs/selected-control/curve.jsonl"
    points = [json.loads(line) for line in curve.read_text().splitlines()]
    assert len(points) == 1 and points[0]["per_task"] == {"chosen": 1.0}
