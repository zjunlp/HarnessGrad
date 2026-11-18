"""SkillOpt strategy.py:129 and strategy.py:165: reflection and consolidation with categorized feedback."""
from __future__ import annotations

import json

import pytest

from methods.policy_ports.skillopt_fast import propose_fast


def test_fast_proposal_can_use_categorized_unscored_feedback():
    prompts = []
    observed = []
    original = {"skills/main.md": "Original guidance"}

    def ask(prompt):
        prompts.append(prompt)
        if "[SkillOpt: merge]" in prompt:
            return {"edits": [{"path": "skills/main.md", "op": "append", "content": "Verify tests."}]}
        return {"edits": [{"path": "skills/main.md", "op": "append", "content": "Run checks."}]}

    def evidence(ids):
        observed.append(ids)
        return json.dumps({"ids": sorted(ids), "feedback": "user correction"})

    def apply_patch(files, edits, budget):
        assert budget == 2
        return {"skills/main.md": files["skills/main.md"] + "\n" + edits[0]["content"]}

    updated, edits = propose_fast(original, [({"task-a"}, {"task-b"})],
        edit_budget=2, merge_batch_size=2, optimizer_memory="", ask=ask,
        evidence=evidence, apply_patch=apply_patch)
    assert observed == [{"task-a"}, {"task-b"}]
    assert [p.split(']')[0] + ']' for p in prompts] == [
        '[SkillOpt: failure]', '[SkillOpt: success]', '[SkillOpt: merge]']
    assert updated == {"skills/main.md": "Original guidance\nVerify tests."}
    assert edits == [{"path": "skills/main.md", "op": "append", "content": "Verify tests."}]
    assert original == {"skills/main.md": "Original guidance"}


def test_no_feedback_does_not_fabricate_a_proposal():
    calls = []
    def forbidden(*args):
        pytest.fail("model or evidence should not be consulted without feedback")
    def apply(files, edits, budget):
        calls.append(edits)
        return dict(files)
    updated, edits = propose_fast({"skills/main.md": "unchanged"}, [(set(), set())],
        edit_budget=2, merge_batch_size=2, optimizer_memory="", ask=forbidden,
        evidence=forbidden, apply_patch=apply)
    assert updated == {"skills/main.md": "unchanged"}
    assert edits == [] and calls == [[]]


def test_invalid_merge_width_fails_before_model_call():
    with pytest.raises(ValueError, match="merge_batch_size"):
        propose_fast({}, [], edit_budget=1, merge_batch_size=1, optimizer_memory="",
                     ask=lambda _: None, evidence=lambda _: "", apply_patch=lambda *a: None)
