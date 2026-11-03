"""upstream opro/__init__.py:93-150: stable best and ascending scored history."""

import json
from native_helpers import runtime, P, fixture, capture, member


def test_opro_stable_best_keeps_earlier_equal_score():
    a, b, c = member(0, 0.5), member(1, 0.8), member(2, 0.8)
    assert P.best([a, b, c]) == b


def test_opro_builds_unique_prompt_candidates_within_measurement_budget(
    tmp_path, monkeypatch
):
    base, req = fixture(tmp_path)
    req["epochs_remaining"] = 3
    output = capture(monkeypatch)
    seen = []

    def ask(prompt, **kwargs):
        seen.append(prompt)
        return {"instruction": f"Novel instruction {len(seen)}\n"}

    monkeypatch.setattr(runtime.editor, "ask", ask)
    assert runtime.run("opro", req) == 0
    assert "failure" not in output, output
    assert len(output["steps"]) == 2  # reserve one state to deploy a measured winner
    for step in output["steps"]:
        assert step["changed"]
        assert (step["harness_dir"] / "harness.json").exists()
    assert "ascending history" in seen[0]


def test_exam_only_point_is_never_a_parent(tmp_path, monkeypatch):
    base, req = fixture(
        tmp_path,
        [
            {
                "round": 0,
                "score_kind": "exam",
                "score": 1,
                "per_task": {"secret": 1},
                "split": {"train": ["t1"], "eval": ["secret"]},
            }
        ],
    )
    out = capture(monkeypatch)
    runtime.run("opro", req)
    assert "no paired" in out["failure"]


def test_unknown_native_knob_is_rejected_instead_of_ignored(monkeypatch):
    import pytest

    monkeypatch.setenv("HG_POLICY_CONFIG", '{"secret_native_evaluator":true}')
    with pytest.raises(ValueError, match="unsupported"):
        runtime.settings("opro")
