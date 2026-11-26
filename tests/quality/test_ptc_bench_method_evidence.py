"""Private benchmark state and held-out verifiers stay outside the method channel."""

import json

import pytest

from harnessgrad.experiment.channel import _stage_task_context


@pytest.mark.parametrize("dataset", ["ptc_bench", "wtm_bench"])
def test_method_context_excludes_private_verifier_inputs(tmp_path, dataset):
    task_id = f"{dataset}/example"
    case = tmp_path / "private.json"
    case.write_text('{"initial_config":"private-state","ground_truth":"private-gold"}')
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "held_out.py").write_text('reference = "held-out-disclosure"')
    verifier = {
        "kind": "command",
        "python_runtime": {"dataset": dataset},
        "argv": ["/usr/bin/python3", "check.py", "--case-sha256", "private-commitment"],
        "inputs": [
            {"path": "private.json", "from": str(case)},
            {"path": "bundle", "from": str(bundle)},
        ],
    }
    result = {"per_task": {task_id: 0}}
    if dataset == "wtm_bench":
        result["verdicts"] = {task_id: {"evidence": {
            "metric": "wtm_bench.workbook_cell_grade",
            "scorer_input": {"sha256": "private-commitment"},
        }}}
    _stage_task_context(
        tmp_path / "method",
        [{"task_id": task_id, "goal": "public goal"}],
        {},
        {task_id: verifier},
        result,
        [task_id],
    )
    page = (tmp_path / "method/_harnessgrad/tasks" / f"{task_id}.json").read_text()
    assert all(value not in page for value in (
        "private-state", "private-gold", "held-out-disclosure", "private-commitment",
    ))
    context = json.loads(page)
    assert context["verifier"]["checks"] == {}
    assert context["goal"] == "public goal" and context["score"] == 0
