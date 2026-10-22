"""The endpoint half of the improver's evidence, kept on disk.

The CLI improver's transcript already lands in `method_logs`, so its reasoning survives a
run. A proposal that goes through the chat endpoint had no such transcript: measured on
2026-10-17, `meta_harness`'s design step and six of `dream_rsi`'s nine rounds were endpoint
calls, and the only remaining trace of *why* those harnesses were proposed was the parsed
edit itself. These tests pin that the conversation is written, that a failed attempt is
written too, and that a write failure never costs a call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))


def _fake_client(reply: str, monkeypatch) -> None:
    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_METHOD_API_KEY", "x")
    monkeypatch.setenv("HG_METHOD_MODEL", "m")
    monkeypatch.setenv("HG_METHOD_PARSE_RETRIES", "1")

    class _Msg:
        content = reply

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = (_Choice(),)
        usage = None
        model = "m"

    class _Completions:
        def create(self, **_kw):
            return _Resp()

    class _Client:
        def __init__(self, **_kw):
            self.chat = type("C", (), {"completions": _Completions()})()

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)


def test_the_conversation_is_written_into_the_round(tmp_path, monkeypatch):
    import editor

    _fake_client('{"files": [], "hypothesis": "because"}', monkeypatch)
    monkeypatch.setenv("HG_CALL_LOG_DIR", str(tmp_path / "method_logs" / "round-3"))

    editor.ask("PROMPT-TEXT")

    files = sorted((tmp_path / "method_logs" / "round-3" / "endpoint").glob("*.json"))
    assert len(files) == 1, "one call, one record"
    record = json.loads(files[0].read_text())
    assert "PROMPT-TEXT" in record["prompt"]
    assert record["reply"].startswith('{"files"')
    assert record["model"] == "m"
    assert record["prompt_chars"] == len(record["prompt"])


def test_an_unparseable_attempt_is_kept_too(tmp_path, monkeypatch):
    """The reply that could not be read is exactly the one someone needs to see."""
    import editor

    _fake_client("I refuse to answer in JSON.", monkeypatch)
    monkeypatch.setenv("HG_CALL_LOG_DIR", str(tmp_path / "r"))
    with pytest.raises(editor.UnparseableReply):
        editor.ask("p")
    files = list((tmp_path / "r" / "endpoint").glob("*.json"))
    assert len(files) == 1
    assert "refuse" in json.loads(files[0].read_text())["reply"]


def test_without_the_driver_path_nothing_is_written(tmp_path, monkeypatch):
    """A method invoked by hand has no round directory; guessing one would scatter files."""
    import editor

    _fake_client('{"files": [], "hypothesis": "x"}', monkeypatch)
    monkeypatch.delenv("HG_CALL_LOG_DIR", raising=False)
    editor.ask("p")
    assert list(tmp_path.rglob("*.json")) == []


def test_a_write_failure_does_not_cost_the_call(tmp_path, monkeypatch):
    """A record that cannot be written must not turn a measured call into a failed one."""
    import editor

    _fake_client('{"files": [], "hypothesis": "x"}', monkeypatch)
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("")                       # a file where a directory has to be
    monkeypatch.setenv("HG_CALL_LOG_DIR", str(blocked))
    reply = editor.ask("p")
    assert reply["hypothesis"] == "x"


def test_the_records_travel_into_the_run_after_the_call(tmp_path):
    """The method can only write where its sandbox allows; the platform moves the record.

    Measured 2026-10-18: pointing the variable at `runs/...` produced zero records, because
    the method's sandbox does not mount the run directory -- the trajectory takes the same
    detour through `method_root`.
    """
    from harnessgrad.loops.mode_a import _keep_endpoint_records

    source = tmp_path / "round1_endpoint" / "endpoint"
    source.mkdir(parents=True)
    (source / "a.json").write_text("{\"prompt\": \"p\"}")
    (source / "b.json").write_text("{\"prompt\": \"q\"}")
    run_dir = tmp_path / "runs" / "rid"

    _keep_endpoint_records(source.parent, run_dir, 1)

    dest = run_dir / "method_logs" / "round-1" / "endpoint"
    assert sorted(p.name for p in dest.glob("*.json")) == ["a.json", "b.json"]
    assert not source.exists(), "the temp copy is removed once it is in the record"


def test_a_record_that_cannot_be_kept_does_not_fail_the_round(tmp_path):
    from harnessgrad.loops.mode_a import _keep_endpoint_records

    source = tmp_path / "round2_endpoint"
    source.mkdir()
    (source / "a.json").write_text("{}")
    run_dir = tmp_path / "runs" / "rid"
    (run_dir / "method_logs").mkdir(parents=True)
    (run_dir / "method_logs" / "round-2").write_text("")     # a file where a dir belongs

    _keep_endpoint_records(source, run_dir, 2)               # must not raise
