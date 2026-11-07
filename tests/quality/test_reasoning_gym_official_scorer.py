"""Reasoning Gym controls use authored data and the prepared scoring artifact."""
from copy import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from data.datasets import reasoning_gym as rg
from data.scaffolds import registry
from data.scaffolds.asset_module import load
from data.scaffolds.qa import control
from eval.run import runner
from harnessgrad import dataset_runtime, dataset_runtime_identity
from test_python_execution_launch import changed_artifact
from tools import datasets

prepare = load(rg.ASSETS / "prepare.py")
TASK_ID = "reasoning_gym/eval/0"


@pytest.fixture
def authored_pool(tmp_path, monkeypatch):
    root = tmp_path / "pool"
    row = {"family": "basic_arithmetic", "index": 0, "question": "What is two plus two?",
           "entry": {"answer": "4", "metadata": {"private": "AUTHORED_PRIVATE_REFERENCE"}}}
    raw = (json.dumps(row) + "\n").encode()
    source = tmp_path / "authored.jsonl"
    source.write_bytes(raw)
    monkeypatch.setattr(rg, "DATASET_ROOT", root)
    monkeypatch.setattr(rg, "POOL_SHA256", hashlib.sha256(raw).hexdigest())
    spec = copy(rg._SPEC)
    spec._records, spec.load_error, spec._load_cause = [], None, None
    monkeypatch.setattr(rg, "_SPEC", spec)
    monkeypatch.setattr(prepare, "POOL", root / "pool.jsonl")
    assert prepare.main(["--import-pool", str(source)]) == 0
    return source


@pytest.fixture
def prepared_runtime(monkeypatch):
    root = os.environ.get("HG_TEST_REASONING_GYM_RUNTIME_ROOT")
    if not root:
        pytest.skip("prepare Reasoning Gym and set HG_TEST_REASONING_GYM_RUNTIME_ROOT")
    monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", str(Path(root).resolve(strict=True)))
    rg._execution_runtime.cache_clear()
    try:
        yield dataset_runtime.resolve_execution("reasoning_gym")
    finally:
        rg._execution_runtime.cache_clear()


def test_frozen_pool_pin_and_family_inventory():
    assert rg.POOL_SHA256 == "c0ff4eaef318cd59afcc61472ebe206362fc0211c4b589ed0d53946a569bd177"
    assert rg.SEED == 20261008 and rg.PER_FAMILY == 20 and len(prepare.FAMILIES) == 89
    assert rg.SPLIT is None and rg.SPLIT_SOURCE == "none" and rg.OFFICIAL_EVAL == []
    declaration = dataset_runtime.execution_descriptor("reasoning_gym")
    assert declaration["pins"][rg.PACKAGE] == "0.1.25" and declaration["wheel_only"]


def test_fetch_points_to_runtime_and_frozen_pool_import(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    monkeypatch.setattr(rg, "DATASET_ROOT", root)
    assert datasets.main(["--fetch", "--dataset", "reasoning_gym"]) == 0
    output = capsys.readouterr().out
    assert "reasoning-gym==0.1.25 (managed execution runtime)" in output
    assert "prepare_dataset.py --dataset reasoning_gym --runtime execution" in output
    assert "prepare.py --import-pool" in output
    assert not root.exists()


def test_loading_and_import_validate_pool_bytes_without_a_runtime(authored_pool, tmp_path, monkeypatch):
    missing = tmp_path / "absent-runtime"
    monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", str(missing))
    tasks, _ = rg.load()
    assert [task["task_id"] for task in tasks] == [TASK_ID]
    assert "AUTHORED_PRIVATE_REFERENCE" not in json.dumps(tasks)
    assert prepare.main(["--check"]) == 0
    assert rg.provision_status()["status"] == "unavailable"
    assert not missing.exists()

    raw = authored_pool.read_bytes()
    authored_pool.write_bytes(raw + b"\n")
    assert prepare.main(["--import-pool", str(authored_pool)]) == 1
    assert prepare.POOL.read_bytes() == raw
    prepare.POOL.write_bytes(raw + b"\n")
    assert prepare.main(["--check"]) == 1
    assert rg.provision_status()["status"] == "invalid"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        rg._rows()


def test_the_library_accepts_reference_and_empty_for_each_generated_family(prepared_runtime, tmp_path):
    # A freshly generated sample per family, independent of the historical pool.
    code = '''import json, sys
from importlib.metadata import version
from reasoning_gym import create_dataset
assert version('reasoning-gym') == '0.1.25'
for family in json.load(sys.stdin):
    dataset = create_dataset(family, size=20, seed=20261008)
    entry = dataset[0]
    assert dataset.score_answer(answer=entry['answer'], entry=entry) == 1.0, family
    assert dataset.score_answer(answer='', entry=entry) == 0.0, family
'''
    proc = subprocess.run([prepared_runtime["python"], "-I", "-B", "-c", code],
                          env={"HOME": str(tmp_path), "PATH": os.defpath, "MPLCONFIGDIR": str(tmp_path)},
                          cwd=tmp_path, input=json.dumps(prepare.FAMILIES), text=True,
                          capture_output=True, timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_public_score_matches_upstream_and_uses_the_selected_python(prepared_runtime):
    gold = {"family": "basic_arithmetic", "entry": {"answer": "4"}}
    for prediction, expected in (("4", 1.0), ("", 0.0), ("4 apples", 0.125)):
        result = rg.score(prediction, gold)
        assert result["reward"] == expected and result["correct"] is (expected == 1.0)
        observation = result["official"]["runtime_observation"]
        assert observation["python"] == prepared_runtime["python"]
        assert Path(observation["package_origin"]).is_relative_to(prepared_runtime["path"])


def test_a_gold_with_no_family_keeps_the_legacy_result():
    result = rg.score("anything", {"answer": "x"})
    assert result["reward"] == 0.0 and "no family" in result["why"]


@pytest.mark.parametrize("oracle", [True, False])
def test_local_qa_control_runs_in_the_bound_environment(authored_pool, prepared_runtime, tmp_path, oracle):
    workdir = tmp_path / "diagnostic"
    assert control.main(["--dataset", "reasoning_gym", "--task-id", TASK_ID,
                         "--oracle" if oracle else "--noop", "--workdir", str(workdir)]) == 0
    evidence = json.loads((workdir / "verifier/evidence.json").read_text())
    assert evidence["measured"] and evidence["reward"] == float(oracle)
    assert evidence["official"]["runtime_observation"]["python"] == prepared_runtime["python"]


def test_sandbox_scores_partial_credit_and_refuses_a_damaged_artifact(
        authored_pool, prepared_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    actor = tmp_path / "actor"
    actor.mkdir()
    (actor / "harness.json").write_text(json.dumps({"name": "authored-reasoning-gym", "version": "1",
                                                   "entrypoint": "agent.py"}))
    (actor / "agent.py").write_text('''import argparse
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--task'); p.add_argument('--workdir'); a=p.parse_args()
work=Path(a.workdir)
assert not (work/'gold.json').exists()
assert 'AUTHORED_PRIVATE_REFERENCE' not in Path(a.task).read_text()
(work/'reasoning_gym.py').write_text('raise AssertionError("actor module entered scorer")')
(work/'answer.txt').write_text('"4 apples"')
''')
    tasks, setups, verifiers = registry.load_tasks("reasoning_gym", task_ids=[TASK_ID])
    identity = dataset_runtime_identity.capture("reasoning_gym", [TASK_ID], setups, verifiers)
    cache = {}
    def measure():
        return runner.evaluate(actor, tasks, {}, setups=setups, verifiers=verifiers,
                               sandbox=True, cache=cache, dataset_runtime_identity=identity,
                               harness_sha="authored-reasoning-gym")
    first = measure()
    assert first["per_task"] == {TASK_ID: 0.125}, first
    verdict = first["verdicts"][TASK_ID]
    evidence = verdict["evidence"]
    assert evidence["scorer_input"]["verified"] and evidence["gold"]["redacted"]
    assert "AUTHORED_PRIVATE_REFERENCE" not in json.dumps(evidence)
    seen = evidence["official"]["runtime_observation"]
    assert seen["python"] == prepared_runtime["python"]
    assert seen["isolated"] and seen["readonly"] and seen["no_bytecode"]
    assert verdict["python_runtime"]["runtime_id"] == prepared_runtime["runtime_id"]
    # Result contracts use fresh measurements. Admission must still precede
    # launching the actor, even with a caller-provided cache.
    assert not cache
    monkeypatch.setattr(runner, "run_one", lambda *a, **kw: pytest.fail("damaged artifact launched an actor"))
    with changed_artifact(prepared_runtime):
        refused = measure()
    assert not refused["cached_tasks"] and not refused["per_task"] and refused["invalid"]
