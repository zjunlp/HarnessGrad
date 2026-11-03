"""Frozen Harbor inventory and real driver controls for explicit grouped splits."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from console import submissions
import driver
from data.datasets import strongreject as dataset
from data.scaffolds import registry
from tools import prepare_h0
from test_strongreject_dataset import endpoint, prepared
from test_strongreject_scoring_identity import run_driver

EVAL_HARNESS = Path(__file__).resolve().parents[1] / "fixtures/base_harness/loop_plain"


def research_split(prepared):
    tasks, _ = prepared.load()
    groups = registry.load_split_groups("strongreject", tasks)
    return tasks, groups, registry.default_split(tasks, split_groups=groups)


def test_strongreject_declares_no_native_split_even_before_provisioning(monkeypatch):
    def absent():
        raise FileNotFoundError("synthetic absent payload")
    monkeypatch.setattr(dataset, "rows", absent)
    assert dataset.SPLIT is None
    assert dataset.SPLIT_SOURCE == "none"
    assert dataset.OFFICIAL_EVAL is None


def test_frozen_research_split_preserves_99_51_and_each_attack_family(prepared):
    rows = prepared.rows()
    tasks, groups, split = research_split(prepared)
    assert registry.load_split("strongreject")[2] is None
    assert len(set(groups.values())) == 50
    for tid, row in rows.items():
        assert groups[tid] == hashlib.sha256(row["forbidden_prompt"].encode()).hexdigest()
    originals = {}
    for side, count in (("train", 33), ("eval", 17)):
        originals[side] = {rows[tid]["forbidden_prompt"] for tid in split[side]}
        assert len(originals[side]) == count
        assert len(split[side]) == count * 3
        assert Counter(rows[tid]["jailbreak_method"] for tid in split[side]) == {
            "prefix_injection": count, "poems": count, "pap_logical_appeal": count}
    assert not originals["train"] & originals["eval"]
    assert set(split["train"]) | set(split["eval"]) == set(rows)
    assert registry.split_sha(split) == "a051b796242fb841353f968209c03016a8e7423d011dd9bd7db7d3460e3e6dbd"
    assert registry.validate_split("strongreject", tasks, split, split_groups=groups) == split
    assert all(set(task) == {"task_id", "goal", "inputs"} for task in tasks)


@pytest.fixture
def h0_selection(monkeypatch):
    """Run the generated H0 command through the real driver, stopping before setup."""
    class SelectedBeforeSetup(Exception):
        pass
    selected = []
    def load_tasks(name, *, task_ids=None):
        assert name == "strongreject"
        selected.extend(task_ids)
        raise SelectedBeforeSetup()
    def launch(command, **kwargs):
        with monkeypatch.context() as child:
            child.setattr(sys, "argv", command[1:])
            child.setattr(os, "environ", kwargs["env"])
            return subprocess.CompletedProcess(command, driver._main({}))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setattr(driver, "load_env", lambda path: {})
    monkeypatch.setattr(registry, "load_tasks", load_tasks)
    monkeypatch.setattr(prepare_h0.subprocess, "run", launch)
    return selected, SelectedBeforeSetup


@pytest.mark.parametrize("split_mode", ["declared", "default", "file"])
def test_h0_study_ids_select_99_tasks_before_setup(prepared, tmp_path, h0_selection, split_mode):
    _, _, split = research_split(prepared)
    ids = tmp_path / "study.txt"
    ids.write_text("\n".join(split["train"]) + "\n")
    if split_mode == "file":
        path = tmp_path / "split.json"
        path.write_text(json.dumps(split))
        split_mode = str(path)
    selected, stop = h0_selection
    with pytest.raises(stop):
        prepare_h0.main(["--dataset", "strongreject", "--split", split_mode,
                         "--study-ids", str(ids), "--runs-root", str(tmp_path / "runs")])
    assert len(selected) == 99
    assert set(selected) == set(split["train"])
    assert not set(selected) & set(split["eval"])
    assert not list((tmp_path / "runs").glob("*/h0.json"))


def test_h0_rejects_other_side_ids_before_setup(prepared, tmp_path, h0_selection, capsys):
    _, _, split = research_split(prepared)
    ids = tmp_path / "wrong-side.txt"
    ids.write_text(split["eval"][0] + "\n")
    with pytest.raises(SystemExit, match="H0 run failed"):
        prepare_h0.main(["--dataset", "strongreject", "--split", "default",
                         "--study-ids", str(ids), "--runs-root", str(tmp_path / "runs")])
    assert h0_selection[0] == []
    assert "not on the train side" in capsys.readouterr().err


def test_old_task_id_default_is_rejected_for_crossing_36_original_requests(prepared):
    tasks, groups, _ = research_split(prepared)
    unsafe = registry.default_split(tasks)
    shared = ({groups[tid] for tid in unsafe["train"]}
              & {groups[tid] for tid in unsafe["eval"]})
    assert len(shared) == 36
    with pytest.raises(ValueError, match="related tasks must stay together"):
        registry.validate_split("strongreject", tasks, unsafe, split_groups=groups)


def test_official_split_keeps_all_150_tasks_in_eval(prepared):
    tasks, groups, _ = research_split(prepared)
    official = registry.official_split("strongreject", tasks)
    assert official == {"train": [], "eval": [task["task_id"] for task in tasks]}
    assert len(official["eval"]) == 150
    assert registry.validate_split("strongreject", tasks, official,
                                   require_nonempty=("eval",), split_groups=groups) == official


def test_split_inventory_reports_none_for_a_provisioned_strongreject(prepared):
    from tools.split_inventory import DASH, _host_row
    row = _host_row("strongreject")
    assert (row.state, row.source, row.tasks) == ("none", "none", "150")
    assert row.train == row.eval == DASH


@pytest.mark.parametrize("side", ["train", "eval"])
def test_driver_default_records_the_grouped_split(run_driver, prepared, tmp_path, side, monkeypatch):
    run_id = "grouped-" + side
    result = run_driver(run_id, side=side,
                        harness_path=EVAL_HARNESS if side == "eval" else None)
    assert result.returncode == 0, result.stdout + result.stderr
    _, _, expected = research_split(prepared)
    run = tmp_path / "runs" / run_id
    meta = json.loads((run / "run_meta.json").read_text())
    point = json.loads((run / "curve.jsonl").read_text().splitlines()[0])
    assert meta["split"] == point["split"] == expected
    assert meta["split_source"] == "default"
    assert meta["split_sha"] == registry.split_sha(expected)

    # The old task-id split also happens to have 99/51 tasks. Compare the actual
    # ids selected by Console readiness with the driver's recorded membership.
    selected = []
    load_envs = registry.load_envs
    def record_envs(name, *, task_ids=None):
        selected.extend(task_ids)
        return load_envs(name, task_ids=task_ids)
    monkeypatch.setattr(registry, "load_envs", record_envs)
    info = submissions._metadata({"dataset": "strongreject", "split": "default", "side": side})
    assert selected == point["split"][side]
    assert info == {"tasks": len(selected), "env_kinds": ["files"], "images": []}


@pytest.mark.parametrize("split_mode", [None, "official"])
def test_driver_without_research_split_keeps_the_whole_pool(run_driver, prepared, tmp_path, split_mode):
    run_id = "whole-pool-" + str(split_mode)
    result = run_driver(run_id, side="eval", split=split_mode, harness_path=EVAL_HARNESS)
    assert result.returncode == 0, result.stdout + result.stderr
    meta = json.loads((tmp_path / "runs" / run_id / "run_meta.json").read_text())
    if split_mode is None:
        assert meta.get("split") is None
        assert meta.get("split_source") is None
        assert meta.get("split_sha") is None
        assert "declares no train/eval split" in result.stdout + result.stderr
    else:
        assert meta["split"] == {"train": [], "eval": list(prepared.rows())}
        assert meta["split_source"] == "official"
        assert meta["split_sha"] == registry.split_sha(meta["split"])


def test_driver_rejects_json_that_scatters_variants_before_selecting_tasks(
        run_driver, prepared, tmp_path, endpoint):
    tasks, _, _ = research_split(prepared)
    path = tmp_path / "unsafe-split.json"
    path.write_text(json.dumps(registry.default_split(tasks)))
    result = run_driver("unsafe-json", "--split", str(path), "--tasks", "unknown/task")
    assert result.returncode == 2
    assert "related tasks must stay together" in result.stdout + result.stderr
    assert not (tmp_path / "runs/unsafe-json").exists()
    assert endpoint["requests"] == []


def test_driver_accepts_grouped_json_and_records_its_source(run_driver, prepared, tmp_path):
    _, _, split = research_split(prepared)
    path = tmp_path / "grouped-split.json"
    path.write_text(json.dumps(split))
    result = run_driver("safe-json", "--split", str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    meta = json.loads((tmp_path / "runs/safe-json/run_meta.json").read_text())
    assert meta["split"] == split
    assert meta["split_source"] == "file:" + str(path.resolve())
    assert meta["split_sha"] == registry.split_sha(split)
