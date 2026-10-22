"""File-backed cards preserve TTHE's gate (text_to_sql/proposer.py:154-193,372-388).

All CLI calls are stubbed; these tests use authored fixtures, never datasets or models.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_tthe import _edit_reply, _invoke, _load, _request, _write_harness


@pytest.fixture
def case(tmp_path):
    mod = _load()
    base = _write_harness(
        tmp_path / "base", traces={"t01": '{"reply": "wrong"}\n'},
        history=[{"round": 0, "score": 0.0, "train_per_task": {"t01": 0.0}}])
    work = tmp_path / "method"
    work.mkdir()
    return mod, _request(base, work, tmp_path / "trajectory.json"), base


@pytest.fixture
def cli(tmp_path, monkeypatch, case):
    """Exercise the actual editor bridge, replacing only the external CLI process."""
    import cli_improver
    import editor

    workspace = tmp_path / "cli"
    workspace.mkdir()
    monkeypatch.setattr(editor.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    monkeypatch.setattr(editor, "_keep_improver_transcript", lambda *args: [])

    def invoke(artifacts=None, *, edits=True, exit_code=0, setup=None):
        def propose(req, parent, **kwargs):
            candidate = Path(req["candidate"])
            candidate.mkdir()
            (candidate / "agent.py").write_text("print('better')\n")
            for rel, content in (artifacts or {}).items():
                target = Path(req["workspace"]) / "artifacts" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.encode("utf-8"))
            if setup:
                setup(Path(req["workspace"]), kwargs["prompt"])
            return {"candidate": candidate, "files": ["agent.py"] if edits else [],
                    "exit_code": exit_code, "note": "CLI explanation"}

        monkeypatch.setattr(cli_improver, "propose", propose)
        base = case[2]
        return editor._ask_cli("method request", base, base, {"name": "stub"})

    return invoke


def _file_reply(content):
    reply = _edit_reply()
    reply.pop("proposal_card")
    reply["artifacts"] = {"proposal_card.json": content}
    return reply


def test_cli_file_card_passes_gate_and_produces_a_step(case, cli, monkeypatch):
    """Upstream proposer.py:163 reads JSON from disk before admitting the child."""
    mod, req, base = case
    content = json.dumps(_edit_reply()["proposal_card"], ensure_ascii=False)
    reply = cli({"proposal_card.json": content})
    assert reply.get("artifacts") == {"proposal_card.json": content}
    got = _invoke(mod, req, monkeypatch, reply=reply)
    assert got["stdout"]["changed"] is True
    record = got["stdout"]["method_reported"]["tthe"]
    assert record["decision"] == "propose"
    assert record["card_valid"] is True
    assert record["proposal_card_source"] == "file: artifacts/proposal_card.json"
    assert mod._card_ok(record["proposal_card"], 1, "cand_round0")
    steps = json.loads(Path(req["trajectory_out"]).read_text())["steps"]
    assert len(steps) == 1
    assert steps[0]["method_reported"]["tthe"] == record
    assert Path(steps[0]["harness_dir"]) != base
    assert (Path(steps[0]["harness_dir"]) / "agent.py").read_text() == "print('better')\n"
    assert not (base / "artifacts").exists()
    assert "artifacts/proposal_card.json" in got["prompt"]


def test_file_reply_alone_opens_gate(case, monkeypatch):
    """Independently red on the old run.py even if the editor bridge is fixed."""
    mod, req, _ = case
    got = _invoke(mod, req, monkeypatch,
                  reply=_file_reply(json.dumps(_edit_reply()["proposal_card"])))
    assert got["stdout"]["changed"] is True
    assert got["stdout"]["method_reported"]["tthe"]["decision"] == "propose"


def test_cli_artifacts_are_verbatim_and_only_from_workspace_artifacts(cli):
    raw = '{\r\n  "说明": "卡片"\r\n}\r\n'

    def setup(workspace, prompt):
        (workspace / "not-an-artifact.txt").write_text("private")
        (workspace / "candidate" / "unrelated.txt").write_text("source")
        assert str(workspace / "artifacts") in prompt

    reply = cli({"nested/card.txt": raw}, setup=setup)
    assert reply["artifacts"] == {"nested/card.txt": raw}
    assert reply["files"] == [{"path": "agent.py", "content": "print('better')\n"}]
    assert reply["hypothesis"] == "CLI explanation"
    assert "no_change" not in reply


def test_cli_artifact_does_not_turn_a_decline_into_an_edit(cli, case, monkeypatch):
    artifacts = {"proposal_card.json": json.dumps(_edit_reply()["proposal_card"])}
    reply = cli(artifacts, edits=False)
    assert reply["no_change"] == "CLI explanation"
    assert reply["artifacts"] == artifacts
    assert "files" not in reply
    got = _invoke(case[0], case[1], monkeypatch, reply=reply)
    assert got["stdout"]["changed"] is False
    assert got["stdout"]["method_reported"]["tthe"]["decision"] == "stop"


@pytest.mark.parametrize("edits", [True, False])
def test_cli_without_artifacts_preserves_existing_reply_fields(cli, edits):
    reply = cli(edits=edits)
    assert reply["artifacts"] == {}
    if edits:
        assert reply["files"] == [{"path": "agent.py", "content": "print('better')\n"}]
        assert reply["hypothesis"] == "CLI explanation"
        assert "no_change" not in reply
    else:
        assert reply["no_change"] == "CLI explanation"
        assert "files" not in reply


def test_cli_deletion_edits_still_survive_artifact_transport(cli):
    def setup(workspace, prompt):
        (workspace / "candidate" / "agent.py").unlink()

    reply = cli({"card.txt": "note"}, setup=setup)
    assert reply["files"] == [{"path": "agent.py", "delete": True}]
    assert reply["hypothesis"] == "CLI explanation"
    assert "no_change" not in reply


def test_cli_crash_without_edits_still_raises(cli):
    import editor

    with pytest.raises(editor.UnparseableReply, match="exited 7 and changed nothing"):
        cli(edits=False, exit_code=7)


@pytest.mark.parametrize("path", ["../proposal_card.json", "/proposal_card.json",
                                   "nested/../card.json", "C:\\card.json",
                                   "nested\\..\\card.json"])
def test_unsafe_artifact_paths_are_reported_and_rejected(case, monkeypatch, path):
    mod, req, base = case
    reply = _file_reply(json.dumps(_edit_reply()["proposal_card"]))
    reply["artifacts"][path] = "untrusted"
    got = _invoke(mod, req, monkeypatch, reply=reply)
    assert got["stdout"]["changed"] is False
    record = got["stdout"]["method_reported"]["tthe"]
    assert record["decision"] == "stop"
    assert record["card_valid"] is False
    assert "file: artifacts/proposal_card.json" in record["reason"]
    assert "unsafe artifact path" in record["reason"]
    assert repr(path) in record["reason"]
    step = json.loads(Path(req["trajectory_out"]).read_text())["steps"][0]
    assert step["harness_dir"] == str(base)


@pytest.mark.parametrize("kind", ["root", "directory", "file"])
def test_cli_rejects_artifact_symlinks_without_reading_targets(cli, tmp_path, kind):
    import editor

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "card.json").write_text("private")

    def setup(workspace, prompt):
        root = workspace / "artifacts"
        if kind == "root":
            if root.exists():
                root.rmdir()
            root.symlink_to(outside, target_is_directory=True)
        else:
            root.mkdir(exist_ok=True)
            (root / "escape").symlink_to(
                outside if kind == "directory" else outside / "card.json",
                target_is_directory=kind == "directory")

    with pytest.raises(editor.UnparseableReply, match="artifact.*symlink"):
        cli(setup=setup)


@pytest.mark.parametrize("artifacts, message", [
    ({"card.txt": "é" * 5}, "per-file limit"),
    ({"one.txt": "1234567", "two.txt": "123456"}, "total limit"),
])
def test_cli_artifact_size_limits_are_bytes_and_never_truncate(
        cli, monkeypatch, artifacts, message):
    import editor

    monkeypatch.setattr(editor, "CLI_ARTIFACT_FILE_LIMIT", 8, raising=False)
    monkeypatch.setattr(editor, "CLI_ARTIFACT_TOTAL_LIMIT", 12, raising=False)
    with pytest.raises(editor.UnparseableReply, match=message):
        cli(artifacts)


def test_cli_artifacts_at_the_limits_are_returned_whole(cli, monkeypatch):
    import editor

    monkeypatch.setattr(editor, "CLI_ARTIFACT_FILE_LIMIT", 8, raising=False)
    monkeypatch.setattr(editor, "CLI_ARTIFACT_TOTAL_LIMIT", 12, raising=False)
    artifacts = {"one.txt": "é" * 4, "two.txt": "1234"}
    assert cli(artifacts)["artifacts"] == artifacts


def test_cli_non_utf8_artifact_is_reported(cli):
    import editor

    def setup(workspace, prompt):
        root = workspace / "artifacts"
        root.mkdir(exist_ok=True)
        (root / "card.json").write_bytes(b"\xff")

    with pytest.raises(editor.UnparseableReply, match="artifact.*UTF-8"):
        cli(setup=setup)


@pytest.mark.parametrize("artifacts", [None, [], "not a mapping"])
def test_malformed_artifact_mapping_is_reported(case, monkeypatch, artifacts):
    mod, req, _ = case
    reply = _file_reply("{}")
    reply["artifacts"] = artifacts
    got = _invoke(mod, req, monkeypatch, reply=reply)
    record = got["stdout"]["method_reported"]["tthe"]
    assert got["stdout"]["changed"] is False
    assert record["decision"] == "stop"
    assert "file: artifacts/proposal_card.json" in record["reason"]
    assert "artifacts must be an object" in record["reason"]


@pytest.mark.parametrize("content, detail", [
    (None, "missing proposal_card.json"),
    ("{bad JSON", "invalid JSON"),
    ("[]", "expected an object"),
    ("{}", "missing behavior_changes, preserved_behaviors, verification, risks"),
    (json.dumps({**_edit_reply()["proposal_card"], "risks": "not a list"}),
     "risks must be a list"),
    (23, "must be UTF-8 text"),
])
def test_invalid_file_cards_keep_incumbent_and_explain_failure(
        case, monkeypatch, content, detail):
    mod, req, base = case
    reply = _file_reply(content)
    if content is None:
        reply["artifacts"] = {}
    got = _invoke(mod, req, monkeypatch, reply=reply)
    assert got["stdout"]["changed"] is False
    record = got["stdout"]["method_reported"]["tthe"]
    assert record["decision"] == "stop"
    assert record["card_valid"] is False
    assert "file: artifacts/proposal_card.json" in record["reason"]
    assert detail in record["reason"]
    step = json.loads(Path(req["trajectory_out"]).read_text())["steps"][0]
    assert step["harness_dir"] == str(base)


@pytest.mark.parametrize("endpoint_valid", [True, False])
def test_endpoint_card_takes_priority_even_when_invalid(case, monkeypatch, endpoint_valid):
    mod, req, _ = case
    reply = _file_reply(json.dumps(_edit_reply()["proposal_card"]))
    reply["proposal_card"] = _edit_reply()["proposal_card"] if endpoint_valid else {}
    got = _invoke(mod, req, monkeypatch, reply=reply)
    assert got["stdout"]["changed"] is endpoint_valid
    record = got["stdout"]["method_reported"]["tthe"]
    assert record["proposal_card_source"] == "reply: proposal_card"
    if not endpoint_valid:
        assert "reply: proposal_card" in record["reason"]
        assert "missing behavior_changes" in record["reason"]


@pytest.mark.parametrize("key", ["behavior_changes", "preserved_behaviors", "verification", "risks"])
@pytest.mark.parametrize("value", [None, "wrong", {}])
def test_card_content_types_remain_strict(case, monkeypatch, key, value):
    mod, req, _ = case
    content = {**_edit_reply()["proposal_card"], key: value}
    got = _invoke(mod, req, monkeypatch, reply=_file_reply(json.dumps(content)))
    assert got["stdout"]["changed"] is False
    record = got["stdout"]["method_reported"]["tthe"]
    assert record["decision"] == "stop"
    assert f"{key} must be a list" in record["reason"]


def test_valid_proposal_card_still_rejects_missing_and_extra_keys(case):
    mod = case[0]
    card = mod._stop_card(1, "cand_round0", "role", "no change", "trace")
    assert mod._card_ok(card, 1, "cand_round0")
    for key in card:
        assert not mod._card_ok({k: v for k, v in card.items() if k != key}, 1, "cand_round0")
    assert not mod._card_ok({**card, "extra": []}, 1, "cand_round0")
