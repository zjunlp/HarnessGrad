"""`random_N` is chosen without a score, so it has to narrow the run *before* round 0.

Until 2026-11-02 the driver applied it after H0: `--sampling random_5` on gsm8k measured 7473
tasks at round 0 and only then looked at five. That is the opposite of what the name promises,
it is why the checked-in `experiments/quickstart.json` could not be run as written, and it made
the cheapest honest policy the most expensive one to *evaluate*.

`below_X` / `above_X` still sweep the side, because there the score is the selection criterion
(INTERFACE.md §2.1) -- the distinction is "needs H0 scores" versus "does not", not "narrows".
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from eval.run.metrics import select_task_set

ROOT = Path(__file__).resolve().parents[2]

DEMO_TRAIN = ["t01", "t02", "t03", "t04"]


def test_random_policy_reads_no_scores() -> None:
    """The selection is a function of the ids and the recorded seed, nothing else."""
    scores = {"t01": 0.0, "t02": 1.0, "t03": 1.0, "t04": 0.0}
    assert select_task_set({}, "random_2", within=DEMO_TRAIN) == \
        select_task_set(scores, "random_2", within=DEMO_TRAIN)
    # Deterministic and order-independent, so two runs cannot disagree about the task set.
    assert select_task_set({}, "random_2", within=DEMO_TRAIN) == \
        select_task_set({}, "random_2", within=list(reversed(DEMO_TRAIN)))
    assert len(select_task_set({}, "random_2", within=DEMO_TRAIN)) == 2


def test_driver_narrows_random_before_h0_and_keeps_score_policies_late() -> None:
    """The actual pre-H0 selection narrows only score-independent policies."""
    from harnessgrad.orchestration.selected_dataset import (
        DatasetPartition, SelectionOptions, select_run_tasks,
    )
    tasks = [{'task_id': tid} for tid in DEMO_TRAIN]
    partition = DatasetPartition(tasks, {}, {'train': DEMO_TRAIN, 'eval': []},
                                 None, None, None, None)
    def selected(policy):
        options = SelectionOptions('demo', 'declared', 'train', None, policy,
                                   'fixture', None, True, False)
        return select_run_tasks(options, partition, None)
    assert selected('random_2').ids == select_task_set({}, 'random_2', within=DEMO_TRAIN)
    for policy in ('below_0.5', 'above_0.5'):
        assert selected(policy).ids == DEMO_TRAIN


def test_random_run_measures_only_the_sampled_tasks(tmp_path: Path) -> None:
    """One task at round 0, and it is the task the policy named -- measured end to end."""
    runs = tmp_path / "runs"
    expected = select_task_set({}, "random_1", within=DEMO_TRAIN)
    proc = subprocess.run(
        [sys.executable, "driver.py", "--dataset", "demo", "--harness", "loop_plain",
         "--side", "train", "--sampling", "random_1", "--rounds", "0",
         "--improver", "none", "--trials", "1", "--jobs", "1",
         "--run-id", "random-one", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"), "--no-sandbox"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
        env={**__import__("os").environ, "HG_AGENT_BACKEND": "mock", "TMPDIR": "/tmp"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    point = json.loads((runs / "random-one" / "curve.jsonl").read_text().splitlines()[0])
    assert point["sampling"]["policy"] == "random_1"
    assert point["sampling"]["task_ids"] == expected
    # The cost the name promises: one task measured, not the whole side.
    assert point["n_scored"] == 1, point["per_task"]
    assert sorted(point["per_task"]) == expected
