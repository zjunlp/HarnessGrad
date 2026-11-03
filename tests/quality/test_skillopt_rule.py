"""FlashRSI skillopt/_patch.py:84-141: exact fast edits preserve slow guidance."""

import pytest
from flashrsi_helpers import runtime, fixture, capture


def test_fast_edits_cannot_replace_protected_epoch_guidance():
    text = "Advice\n<!-- SLOW_UPDATE_START -->\nProtect\n<!-- SLOW_UPDATE_END -->\n"
    with pytest.raises(ValueError, match="protected"):
        runtime.apply_skill_patch(
            {"skills/main.md": text},
            [
                {
                    "op": "replace",
                    "path": "skills/main.md",
                    "target": "Protect",
                    "content": "Gone",
                }
            ],
            1,
        )
    result = runtime.apply_skill_patch(
        {"skills/main.md": text},
        [{"op": "append", "path": "skills/main.md", "content": "More"}],
        1,
    )
    assert text.strip() in result["skills/main.md"]


def test_fast_reflection_splits_failure_success_then_merges(tmp_path, monkeypatch):
    base, req = fixture(tmp_path)
    out = capture(monkeypatch)
    prompts = []

    def ask(prompt, **kw):
        prompts.append(prompt)
        if "[SkillOpt: merge]" in prompt:
            return {
                "edits": [
                    {
                        "path": "skills/main.md",
                        "op": "append",
                        "content": "Verify output files.",
                    }
                ]
            }
        return {
            "edits": [
                {
                    "path": "skills/main.md",
                    "op": "append",
                    "content": "Shared procedure.",
                }
            ]
        }

    monkeypatch.setattr(runtime.editor, "ask", ask)
    runtime.run("skillopt", req)
    assert "failure" not in out, out
    assert (
        any("[SkillOpt: success]" in s for s in prompts)
        and any("[SkillOpt: failure]" in s for s in prompts)
        and any("[SkillOpt: merge]" in s for s in prompts)
    )


def test_slow_phase_replaces_protected_guidance_and_records_optimizer_memory(
    tmp_path, monkeypatch
):
    points = [
        {
            "round": 0,
            "score_kind": "training",
            "score": 0.5,
            "per_task": {"t1": 1, "t2": 0},
            "method_reported": {"flashrsi_port": {"invocation": 1}},
        }
    ]
    base, req = fixture(tmp_path, points)
    out = capture(monkeypatch)
    (base / "skills/main.md").write_text(
        "Keep\n<!-- SLOW_UPDATE_START -->\nOld\n<!-- SLOW_UPDATE_END -->\n"
    )
    replies = iter(
        [
            {"text": "Prefer evidence before revisions."},
            {
                "guidance": [
                    {"path": "skills/main.md", "content": "New evidence-backed policy"}
                ]
            },
        ]
    )
    monkeypatch.setattr(runtime.editor, "ask", lambda *a, **k: next(replies))
    runtime.run("skillopt", req)
    assert "failure" not in out, out
    info = out["steps"][0]["method_reported"]["flashrsi_port"]
    assert info["phase"] == "slow" and "evidence" in info["meta_skill"]
    text = (out["steps"][0]["harness_dir"] / "skills/main.md").read_text()
    assert "Keep" in text and "Old" not in text and "New evidence-backed policy" in text
