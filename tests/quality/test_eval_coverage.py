"""An eval point keeps the full planned selection when some results are missing."""
import json
from types import SimpleNamespace

import pytest

from harnessgrad.loops import eval_side
from harnessgrad import verification_context


@pytest.mark.parametrize("resume", [False, True])
def test_eval_sampling_keeps_planned_ids_and_scores_only_measured_ids(tmp_path, monkeypatch, resume):
    tasks = [{"task_id": name} for name in ("a", "b", "c")]
    args = SimpleNamespace(from_run="train", from_round=0, mode="A", trials=1,
                           jobs=1, sandbox=False, dataset="demo", resume=resume)
    args.verification_context = verification_context.capture(tasks, {}, {}, {}, {}, sandbox=args.sandbox)
    monkeypatch.setenv("HARNESSGRAD_SEED", "coverage-seed")
    monkeypatch.setattr(eval_side, "commit_state", lambda *a: "sha")
    monkeypatch.setattr(eval_side, "_identity", lambda *a, **kw: {})
    monkeypatch.setattr(eval_side, "_env_record", lambda *a: {})
    monkeypatch.setattr(eval_side, "_save_verdicts", lambda *a: None)
    monkeypatch.setattr(eval_side, "_save_traces", lambda *a, **kw: None)
    monkeypatch.setattr(eval_side.console, "results", lambda *a, **kw: None)
    # The historical measurement was taken under the same explicit policy.
    metadata = tmp_path / "run_meta.json"
    metadata.write_text(json.dumps({"evaluated_from": {"run_id": "train", "round": 0},
                                   **verification_context.fields(args.verification_context)}))
    historical = {metadata: metadata.read_bytes()}
    if resume:
        progress = tmp_path / "progress/round-0/a.json"
        progress.parent.mkdir(parents=True)
        progress.write_text(json.dumps({
            "task_id": "a", "complete": True, "score": .5,
            "process_outcome": "completed", "n_trials": 1,
            "verification_context": verification_context.project(args.verification_context, ["a"])}))
        historical[progress] = progress.read_bytes()
    result = {"per_task": {} if resume else {"a": .5}, "trials": 1,
              "invalid": {"b": {"stage": "scorer", "detail": "fixture refusal"}}}
    evaluated = []
    def evaluate(work, selected, *a, **kw):
        evaluated.append([task["task_id"] for task in selected])
        selected_context = verification_context.project(kw["verification_context"], evaluated[-1])
        assert selected_context == verification_context.capture(selected, {}, {}, {}, {}, sandbox=args.sandbox)
        assert [t["task_id"] for t in selected] == (["b", "c"] if resume else ["a", "b", "c"])
        return {**result, **verification_context.fields(selected_context)}
    monkeypatch.setattr(eval_side, "evaluate", evaluate)
    assert eval_side._run_eval_side(
        work=tmp_path, man={}, run_id="eval", run_dir=tmp_path, tasks=tasks,
        scorable={}, setups={}, verifiers={}, envs={}, side_ids=["a", "b", "c"],
        split=None, args=args, eval_from={"run_id": "train", "round": 0}) == 0
    point = json.loads((tmp_path / "curve.jsonl").read_text())
    assert point["verification_context"] == args.verification_context
    assert point["sampling"]["task_ids"] == ["a", "b", "c"]
    assert point["per_task"] == {"a": .5} and point["n_scored"] == 1
    assert point["n_invalid"] == 1 and "b" in point["invalid"]
    assert evaluated == ([["b", "c"]] if resume else [["a", "b", "c"]])
    assert all(path.read_bytes() == body for path, body in historical.items())
