"""upstream evo_harness/__init__.py:135-211: memory provenance and level gate."""

from native_helpers import runtime, fixture, capture


def test_topic_skill_uses_current_failed_memory_and_is_native_discoverable(
    tmp_path, monkeypatch
):
    base, req = fixture(tmp_path)
    out = capture(monkeypatch)
    replies = iter(
        [
            {
                "lesson": "check status",
                "trigger": "tool call",
                "evidence": "nonzero exit",
                "scope_hint": "shell",
            },
            {
                "skills": [
                    {
                        "path": "skills/shell/SKILL.md",
                        "name": "shell",
                        "description": "Check shell errors",
                        "source_task_ids": ["t2"],
                        "content": "Inspect the return status.",
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(runtime.editor, "ask", lambda *a, **k: next(replies))
    runtime.run("evo_harness", req)
    assert "failure" not in out, out
    file = out["steps"][0]["harness_dir"] / "skills/shell/SKILL.md"
    assert "description" in file.read_text() and "evo_harness" in file.read_text()


def test_skill_cannot_cite_task_outside_current_memories(tmp_path, monkeypatch):
    base, req = fixture(tmp_path)
    out = capture(monkeypatch)
    replies = iter(
        [
            {
                "lesson": "check",
                "trigger": "tool",
                "evidence": "exit",
                "scope_hint": "shell",
            },
            {
                "skills": [
                    {
                        "path": "skills/shell/SKILL.md",
                        "name": "shell",
                        "description": "desc",
                        "source_task_ids": ["unseen"],
                        "content": "Check.",
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(runtime.editor, "ask", lambda *a, **k: next(replies))
    runtime.run("evo_harness", req)
    assert "current-batch" in out["failure"]
