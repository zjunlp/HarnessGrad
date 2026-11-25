"""Private benchmark state and held-out verifiers stay outside the method channel."""

import json

from harnessgrad.experiment.channel import _stage_task_context


def test_ptc_bench_method_context_excludes_private_verifier_inputs(tmp_path):
    task_id = "ptc_bench/multi_turn/example"
    case = tmp_path / "private.json"
    case.write_text('{"initial_config":"private-state","ground_truth":"private-gold"}')
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "held_out.py").write_text('reference = "held-out-disclosure"')
    verifier = {
        "kind": "command",
        "python_runtime": {"dataset": "ptc_bench"},
        "argv": ["/usr/bin/python3", "check.py", "--case-sha256", "private-commitment"],
        "inputs": [
            {"path": "ptc_private_case.json", "from": str(case)},
            {"path": "ptc_bundle", "from": str(bundle)},
        ],
    }
    _stage_task_context(
        tmp_path / "method",
        [{"task_id": task_id, "goal": "public goal"}],
        {},
        {task_id: verifier},
        {"per_task": {task_id: 0}},
        [task_id],
    )
    page = (tmp_path / "method/_harnessgrad/tasks" / f"{task_id}.json").read_text()
    assert all(value not in page for value in (
        "private-state", "private-gold", "held-out-disclosure", "private-commitment",
    ))
    context = json.loads(page)
    assert context["verifier"]["checks"] == {}
    assert context["goal"] == "public goal" and context["score"] == 0
