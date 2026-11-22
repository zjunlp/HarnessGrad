"""Exercise the policy JSON-role boundary through an authored CLI process."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys

import pytest

from native_helpers import fixture, runtime


@pytest.fixture
def role(tmp_path, monkeypatch):
    base, req = fixture(tmp_path)
    # Distinguish the role's projected prompt from files available to the method.
    (base / "prompt.md").write_text("UNSELECTED_PARENT_CONTENT")
    (base / "_harnessgrad" / "tasks" / "t2.json").write_text("UNSELECTED_TASK_CONTENT")
    launcher = tmp_path / "authored-dsh"
    launcher.write_text(f"#!{sys.executable}\n" + r'''
import json, sys
from pathlib import Path

prompt = sys.argv[-1]
assert "SELECTED_FEEDBACK" in prompt
assert "UNSELECTED_" not in prompt
assert not Path("prompt.md").exists()
assert not Path("agent.py").exists()
assert not Path("_harnessgrad").exists()
assert not Path("evidence/tasks/t2.json").exists()
assert "role-result.json" in prompt
output = Path("../artifacts/role-result.json")
if "WRITE_TO_TREE" in prompt:
    Path("unexpected.py").write_text("out-of-scope")
if "OMIT_ARTIFACT" not in prompt:
    output.write_text("[]" if "NON_OBJECT" in prompt else json.dumps({"instruction":"Check before reporting."}))
print('dsh-usage {"input_tokens": 11, "output_tokens": 4, "cache_read_tokens": 0, "total_tokens": 15}')
if "FAIL_AFTER_WRITE" in prompt:
    sys.exit(7)
''')
    launcher.chmod(0o700)
    record = base / "_harnessgrad" / "round.json"
    point = json.loads(record.read_text())
    point["identity"]["improver"] = {
        "name": "authored-dsh", "kind": "cli", "argv_style": "dsh",
        "path": str(launcher), "model": "fixture-flash", "provider": "fixture-provider",
        "profile": "headless",
    }
    record.write_text(json.dumps(point))
    monkeypatch.chdir(req["workspace"])
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    monkeypatch.delenv("HG_IMPROVER_ARGS", raising=False)
    monkeypatch.setenv("HG_IMPROVER_DSH_HOME", str(tmp_path / "empty-dsh-home"))
    monkeypatch.setattr(runtime.editor, "_TERMINAL_STOP", {})
    monkeypatch.setattr(runtime.editor, "_INVOCATION_TOKEN_LIMIT", None)
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("unexpected direct endpoint"))
    runtime.editor.reset_spent()
    yield base
    runtime.editor.reset_spent()


def test_policy_meta_harness_returns_only_the_requested_artifact(role):
    reply = runtime.schema("[OPRO: propose] SELECTED_FEEDBACK", role)
    assert reply == {"instruction": "Check before reporting."}
    assert (role / "prompt.md").read_text() == "UNSELECTED_PARENT_CONTENT"
    usage = runtime.editor.improver_run()
    assert usage["usage"] == {"input": 11, "output": 4, "cache_read": 0, "calls": 1}


@pytest.mark.parametrize("case", ["WRITE_TO_TREE", "OMIT_ARTIFACT", "NON_OBJECT", "FAIL_AFTER_WRITE"])
def test_policy_meta_harness_refuses_unusable_or_out_of_scope_result(role, case):
    with pytest.raises((runtime.editor.UnparseableReply, ValueError)):
        runtime.schema(f"[SkillOpt: rank] SELECTED_FEEDBACK {case}", role)
    assert (role / "prompt.md").read_text() == "UNSELECTED_PARENT_CONTENT"
    assert not (role / "unexpected.py").exists()
    assert len(list(Path.cwd().glob("*-usage-ledger.log"))) == 1
