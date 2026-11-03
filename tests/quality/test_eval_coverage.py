"""An eval point keeps the full planned selection when some results are missing."""
import json
from types import SimpleNamespace

import pytest

from harnessgrad.loops import eval_side


@pytest.mark.parametrize("resume", [False, True])
def test_eval_sampling_keeps_planned_ids_and_scores_only_measured_ids(tmp_path, monkeypatch, resume):
    tasks = [{"task_id": name} for name in ("a", "b", "c")]
    args = SimpleNamespace(from_run="train", from_round=0, mode="A", trials=1,
                           jobs=1, sandbox=False, dataset="demo", resume=resume)
    monkeypatch.setenv("HARNESSGRAD_SEED", "coverage-seed")
    monkeypatch.setattr(eval_side, "commit_state", lambda *a: "sha")
    monkeypatch.setattr(eval_side, "_identity", lambda *a, **kw: {})
    monkeypatch.setattr(eval_side, "_env_record", lambda *a: {})
    monkeypatch.setattr(eval_side, "_save_verdicts", lambda *a: None)
    monkeypatch.setattr(eval_side, "_save_traces", lambda *a, **kw: None)
    monkeypatch.setattr(eval_side.console, "results", lambda *a, **kw: None)
    if resume:
        monkeypatch.setattr(eval_side, "_progress_results", lambda *a: {
            "a": {"score": .5, "process_outcome": "completed", "n_trials": 1}})
        monkeypatch.setattr(eval_side, "_resume_mismatch", lambda *a: None)
        monkeypatch.setattr(eval_side, "_resume_runtime_mismatch", lambda *a: None)
    result = {"per_task": {} if resume else {"a": .5}, "trials": 1,
              "invalid": {"b": {"stage": "scorer", "detail": "fixture refusal"}}}
    def evaluate(work, selected, *a, **kw):
        assert [t["task_id"] for t in selected] == (["b", "c"] if resume else ["a", "b", "c"])
        return result
    monkeypatch.setattr(eval_side, "evaluate", evaluate)
    assert eval_side._run_eval_side(
        work=tmp_path, man={}, run_id="eval", run_dir=tmp_path, tasks=tasks,
        scorable={}, setups={}, verifiers={}, envs={}, side_ids=["a", "b", "c"],
        split=None, args=args, eval_from={"run_id": "train", "round": 0}) == 0
    point = json.loads((tmp_path / "curve.jsonl").read_text())
    assert point["sampling"]["task_ids"] == ["a", "b", "c"]
    assert point["per_task"] == {"a": .5} and point["n_scored"] == 1
    assert point["n_invalid"] == 1 and "b" in point["invalid"]
