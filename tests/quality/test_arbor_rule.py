"""FlashRSI arbor/__init__.py:138-238: bounded hypothesis tree and trunk origin."""

from flashrsi_helpers import runtime, fixture, capture


def test_coordinator_expands_twice_then_runs_leaf_from_trunk(tmp_path, monkeypatch):
    base, req = fixture(tmp_path)
    out = capture(monkeypatch)
    answers = iter(
        [
            {"action": "expand", "node_id": "ROOT", "hypotheses": ["tool reliability"]},
            {"action": "expand", "node_id": "1", "hypotheses": ["check return codes"]},
            {"action": "run", "node_id": "1.1"},
            {
                "files": [
                    {"path": "agent.py", "find": "fixture", "replace": "checked"}
                ],
                "hypothesis": "leaf implementation",
            },
        ]
    )
    monkeypatch.setattr(runtime.editor, "ask", lambda *a, **k: next(answers))
    runtime.run("arbor", req)
    assert "failure" not in out, out
    info = out["steps"][0]["method_reported"]["flashrsi_port"]
    assert (
        info["node_id"] == "1.1"
        and info["parent_round"] == 0
        and info["state"]["nodes"]["1.1"]["status"] == "running"
    )


def test_measured_regression_reflects_and_stop_restores_trunk(tmp_path, monkeypatch):
    state = {
        "nodes": {
            "ROOT": {
                "id": "ROOT",
                "parent_id": None,
                "depth": 0,
                "status": "root",
                "hypothesis": "root",
                "insight": "",
            },
            "1": {
                "id": "1",
                "parent_id": "ROOT",
                "depth": 1,
                "status": "pending",
                "hypothesis": "branch",
                "insight": "",
            },
            "1.1": {
                "id": "1.1",
                "parent_id": "1",
                "depth": 2,
                "status": "running",
                "hypothesis": "experiment",
                "insight": "",
            },
        },
        "trunk_round": 0,
        "processed_rounds": [],
    }
    points = [
        {
            "round": 0,
            "score_kind": "training",
            "score": 1,
            "per_task": {"t1": 1, "t2": 1},
        },
        {
            "round": 1,
            "score_kind": "training",
            "score": 0.5,
            "per_task": {"t1": 1, "t2": 0},
            "method_reported": {
                "flashrsi_port": {"node_id": "1.1", "state": state, "invocation": 1}
            },
        },
    ]
    base, req = fixture(tmp_path, points)
    out = capture(monkeypatch)
    seen = []

    def ask(prompt, **kw):
        seen.append(prompt)
        if "[Arbor: coordinate]" in prompt:
            return {"action": "stop", "reason": "no support"}
        return {"text": "measured regression; preserve the trunk"}

    monkeypatch.setattr(runtime.editor, "ask", ask)
    runtime.run("arbor", req)
    assert "failure" not in out, out
    info = out["steps"][0]["method_reported"]["flashrsi_port"]
    assert info["parent_round"] == 0 and out["stop"] is True
    assert info["state"]["nodes"]["1.1"]["status"] == "done"
    assert "regression" in info["state"]["nodes"]["ROOT"]["insight"]
    assert len([p for p in seen if "[Arbor: synthesize]" in p]) == 2
