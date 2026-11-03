"""FlashRSI modular_rsi/__init__.py:77-166: isolated module branches and scopes."""

import json
from flashrsi_helpers import runtime, fixture, capture


def test_missing_real_trial_contrast_is_refused(tmp_path, monkeypatch):
    base, req = fixture(tmp_path)
    out = capture(monkeypatch)
    monkeypatch.setenv(
        "HG_FLASHRSI_CONFIG",
        json.dumps({"modules": {"runtime": ["agent.py"]}, "num_rollouts": 2}),
    )
    runtime.run("modular_rsi", req)
    assert "--trials" in out["failure"]


def test_module_scope_cannot_modify_an_unowned_file(tmp_path, monkeypatch):
    points = [
        {
            "round": 0,
            "score_kind": "training",
            "score": 0.5,
            "per_task": {"t1": 1, "t2": 0},
            "per_task_trials": {"t1": [1, 0], "t2": [0, 0]},
        }
    ]
    base, req = fixture(tmp_path, points)
    out = capture(monkeypatch)
    monkeypatch.setenv(
        "HG_FLASHRSI_CONFIG",
        json.dumps({"modules": {"runtime": ["agent.py"]}, "num_rollouts": 2}),
    )
    answers = iter(
        [
            {"findings": [{"target": "agent.py", "problem": "x", "fix": "y"}]},
            {"files": [{"path": "prompt.md", "find": "Baseline", "replace": "Hacked"}]},
        ]
    )
    monkeypatch.setattr(runtime.editor, "ask", lambda *a, **k: next(answers))
    runtime.run("modular_rsi", req)
    assert "out-of-scope" in out["failure"]
