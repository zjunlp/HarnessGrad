"""FlashRSI ace/_playbook.py:56-157: ADD-only curation and usage tags."""

import json
import pytest
from flashrsi_helpers import runtime, fixture, capture


def test_ace_add_cannot_delete_or_rewrite_old_playbook(tmp_path, monkeypatch):
    base, req = fixture(tmp_path)
    out = capture(monkeypatch)
    replies = iter(
        [
            {"key_insight": "verify tool success", "bullet_tags": []},
            {
                "operations": [
                    {
                        "type": "ADD",
                        "section": "general",
                        "content": "Check shell exit codes.",
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(runtime.editor, "ask", lambda *a, **k: next(replies))
    runtime.run("ace", req)
    assert "failure" not in out, out
    text = (out["steps"][0]["harness_dir"] / "prompt.md").read_text()
    assert text.startswith("Baseline instruction") and "helpful=0 harmful=0" in text


def test_ace_refuses_non_add_operation():
    native = runtime._load(
        runtime.Path(runtime.__file__).parent / "vendor/ace/_playbook.py",
        "_test_native_ace",
    )
    with pytest.raises(native.PlaybookError):
        native.Patch.parse(
            json.dumps({"operations": [{"type": "DELETE", "content": "x"}]})
        )
    book = native.Patch.parse(
        json.dumps({"operations": [{"type": "ADD", "content": "x"}]})
    ).apply("", 1)
    assert "helpful=1" in native.tag(book, (("g-00001", "helpful"),))
