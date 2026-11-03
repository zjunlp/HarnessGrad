"""upstream adas/__init__.py:111-151: reflect twice before platform measurement."""

from native_helpers import runtime, fixture, capture


def test_adas_reflects_on_the_already_modified_design(tmp_path, monkeypatch):
    base, req = fixture(tmp_path)
    out = capture(monkeypatch)
    seen = []

    def ask(prompt, **kw):
        parent = kw["parent"]
        seen.append((prompt, (parent / "agent.py").read_text()))
        if "reflect" in prompt:
            return {"no_change": "already valid"}
        return {
            "files": [
                {
                    "path": "agent.py",
                    "find": 'print("fixture")',
                    "replace": 'print("changed")',
                }
            ],
            "hypothesis": "new design",
        }

    monkeypatch.setattr(runtime.editor, "ask", ask)
    runtime.run("adas", req)
    assert "failure" not in out, out
    assert len(seen) == 3 and "changed" in seen[1][1] and "changed" in seen[2][1]
    assert (
        out["steps"][0]["harness_dir"] / "agent.py"
    ).read_text() == 'print("changed")\n'
