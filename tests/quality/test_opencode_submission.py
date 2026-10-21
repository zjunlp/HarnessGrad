"""Submission transport, including blank CLI completions; no network or model."""
import importlib.util
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def adapter():
    spec = importlib.util.spec_from_file_location(
        "opencode_submission_adapter", ROOT / "base_harness/opencode_agent/agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def files_task(*paths):
    return {"task_id": "opaque", "goal": "Create the requested artifacts.",
            "submission": {"kind": "files", "paths": list(paths)}}


@pytest.mark.parametrize("final", ["", " \n", "Finished"])
def test_declared_files_complete_without_synthetic_answer(adapter, tmp_path, final):
    (tmp_path / "solution.py").write_bytes(b"print(42)\n")
    (tmp_path / "result.bin").write_bytes(b"\xff\x00")
    task = files_task("solution.py", "result.bin")
    assert adapter.save_answer(tmp_path, None, final, task=task) == ("", "files")
    assert not (tmp_path / "answer.txt").exists()
    assert (tmp_path / "result.bin").read_bytes() == b"\xff\x00"


def test_file_completion_does_not_grade_or_require_changed_contents(adapter, tmp_path):
    # Empty or unchanged artifacts can be valid; contents belong to the scorer.
    (tmp_path / "empty.txt").touch()
    (tmp_path / "answer.txt").write_text("existing, unrelated answer")
    before = adapter.answer_stamp(tmp_path)
    assert adapter.save_answer(tmp_path, before, "", task=files_task("empty.txt")) == ("", "files")
    assert (tmp_path / "answer.txt").read_text() == "existing, unrelated answer"


@pytest.mark.parametrize("present", ["absent", "directory", "partial"])
def test_missing_artifacts_cannot_be_replaced_by_final_prose(adapter, tmp_path, present):
    if present == "directory":
        (tmp_path / "solution.py").mkdir()
    if present == "partial":
        (tmp_path / "solution.py").write_text("pass")
    with pytest.raises(ValueError, match="submission file"):
        adapter.save_answer(tmp_path, None, "Done", task=files_task("solution.py", "report.json"))
    assert not (tmp_path / "answer.txt").exists()


@pytest.mark.parametrize("descriptor", [
    None, {}, {"kind": "unknown"}, {"kind": "files", "paths": []},
    {"kind": "files", "paths": "solution.py"}, {"kind": "files", "paths": [7]},
    {"kind": "files", "paths": ["../outside.txt"]},
    {"kind": "files", "paths": ["/tmp/outside.txt"]},
    {"kind": "files", "paths": ["C:\\outside.txt"]},
    {"kind": "files", "paths": ["a/../../outside.txt"]},
    {"kind": "files", "paths": ["answer.txt"], "expected": "secret"},
    {"kind": "text", "paths": ["solution.py"]},
])
def test_invalid_public_submission_is_rejected_before_launch(adapter, descriptor):
    task = {"goal": "Do the task", "submission": descriptor}
    with pytest.raises(ValueError, match="submission"):
        adapter.task_prompt(task)


def test_artifact_symlink_cannot_escape_workdir(adapter, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("not submitted")
    work = tmp_path / "work"
    work.mkdir()
    (work / "solution.py").symlink_to(outside)
    with pytest.raises(ValueError, match="submission file"):
        adapter.save_answer(work, None, "", task=files_task("solution.py"))


@pytest.mark.parametrize("task", [{"goal": "Answer"}, {"goal": "Answer", "submission": {"kind": "text"}}])
def test_text_submission_rejects_blank_answer_even_with_scratch_files(adapter, tmp_path, task):
    (tmp_path / "scratch.py").write_text("print(42)")
    with pytest.raises(ValueError):
        adapter.save_answer(tmp_path, None, " \n", task=task)
    (tmp_path / "answer.txt").write_text(" \n")
    with pytest.raises(ValueError):
        adapter.save_answer(tmp_path, None, "", task=task)


def test_text_submission_preserves_tool_json_and_final_fallback(adapter, tmp_path):
    task = {"goal": "Answer", "submission": {"kind": "text"}}
    raw = '{"answer":"42","evidence":[1]}\n'
    (tmp_path / "answer.txt").write_text(raw)
    assert adapter.save_answer(tmp_path, None, "Done", task=task) == (raw, "artifact")
    assert adapter.save_answer(tmp_path, adapter.answer_stamp(tmp_path), "43", task=task) == ("43", "final_message")


def test_prompt_renders_public_file_contract_without_text_requirement(adapter):
    prompt = adapter.task_prompt(files_task("nested/output.json", "image.bin"))
    assert "nested/output.json" in prompt and "image.bin" in prompt
    assert "Otherwise your final message is the answer" not in prompt


def run_replay(adapter, tmp_path, monkeypatch, *, task, final="", artifact=True,
               finish="stop", outcome="succeeded", provider_error=False):
    """Replace only the external CLI; run the actual wrapper and filesystem writes."""
    harness = tmp_path / "harness"
    source = harness / "opencode/packages/cli/src"
    source.mkdir(parents=True)
    (source / "index.ts").write_text("// CLI boundary replaced in this offline test")
    (harness / "harness.json").write_text(json.dumps({"runtime": {
        "kind": "bun", "source": "opencode", "entrypoint": "packages/cli/src/index.ts"}}))
    work = tmp_path / "task"
    work.mkdir()
    monkeypatch.setattr(adapter, "ROOT", harness)
    tokens = {"input": 80, "output": 10, "reasoning": 2, "cache": {"read": 5, "write": 0}}
    snapshot = {"info": {"id": "s1", "outcome": outcome, "tokens": tokens}, "messages": [
        {"id": "m1", "type": "assistant", "finish": finish, "tokens": tokens,
         "content": [{"type": "text", "text": final}]}]}
    event = {"type": "step_finish", "sessionID": "s1", "part": {
        "id": "finish1", "messageID": "m1", "reason": finish, "tokens": tokens}}
    stdout = json.dumps(event) + "\n"
    if provider_error:
        stdout += json.dumps({"type": "error", "error": {"type": "provider.internal", "status": 503}}) + "\n"

    class CLI:
        returncode = 0

        def __init__(self, argv, **kwargs):
            self.export = "export" in argv

        def communicate(self, *args, **kwargs):
            if not self.export and artifact:
                (work / "solution.py").write_text("print(42)\n")
            return (json.dumps(snapshot) if self.export else stdout), ""

    monkeypatch.setattr(adapter.subprocess, "Popen", CLI)
    with patch.dict(os.environ, {"HG_AGENT_MODEL": "fixture", "HG_AGENT_API_KEY": "fixture-only",
                                "HG_AGENT_BASE_URL": "https://example.invalid/v1"}, clear=True):
        code = adapter.run(task, work)
    rows = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
    return code, rows, work


@pytest.mark.parametrize("final", ["", " "])
def test_completed_file_session_accepts_blank_final_and_records_usage(adapter, tmp_path, monkeypatch, final):
    code, rows, work = run_replay(adapter, tmp_path, monkeypatch, task=files_task("solution.py"), final=final)
    assert code == 0
    assert next(r for r in rows if r.get("submitted"))["answer_source"] == "files"
    usage = [r["usage"] for r in rows if "usage" in r]
    assert len(usage) == 1 and usage[0]["input_tokens"] == 85 and usage[0]["output_tokens"] == 12
    assert not (work / "answer.txt").exists()


@pytest.mark.parametrize("failure", ["missing", "text", "truncated", "failed", "provider"])
def test_failed_completion_keeps_usage_and_never_claims_submission(adapter, tmp_path, monkeypatch, failure):
    task = {"goal": "Answer 42"} if failure == "text" else files_task("solution.py")
    code, rows, _ = run_replay(adapter, tmp_path, monkeypatch, task=task,
                              artifact=failure != "missing",
                              finish="length" if failure == "truncated" else "stop",
                              outcome="failed" if failure == "failed" else "succeeded",
                              provider_error=failure == "provider")
    assert code == 1
    assert not any(r.get("submitted") for r in rows)
    usage = [r["usage"] for r in rows if "usage" in r]
    assert len(usage) == 1 and usage[0]["input_tokens"] == 85


def test_livecodebench_declares_only_public_output_path(monkeypatch):
    # Replace only eager private-data loading; execute the adapter's real load/goal.
    from data.scaffolds import scorer_input
    row = {"question_id": "synthetic", "question_content": "A fabricated question.",
           "question_title": "Fixture", "platform": "fixture", "contest_id": "fixture",
           "contest_date": "2026-01-01", "difficulty": "easy", "starter_code": "",
           "n_public": 0, "n_private": 0}
    pool = json.dumps({"problems": 1055}) + "\n" + (json.dumps(row) + "\n") * 1055
    original_open = Path.open
    original_glob = Path.glob

    def open_fixture(path, *args, **kwargs):
        if path.name == "pool.jsonl" and path.parent.name == "livecodebench":
            return io.StringIO(pool)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_fixture)
    monkeypatch.setattr(Path, "glob", lambda p, pattern: iter(())
                        if p.name == "references" and p.parent.name == "livecodebench"
                        else original_glob(p, pattern))
    monkeypatch.setattr(scorer_input, "bind", lambda spec, *args: spec)
    spec = importlib.util.spec_from_file_location("lcb_submission_fixture", ROOT / "data/datasets/livecodebench.py")
    dataset = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dataset)
    tasks, _ = dataset.load()
    assert tasks[0]["submission"] == {"kind": "files", "paths": ["solution.py"]}
    assert "solution.py" in tasks[0]["goal"]
    from eval.run.task_contract import runtime_task
    projected = runtime_task(tasks[0], {"runtime_prompt": "Use the public CLI."})
    assert projected["submission"] == {"kind": "files", "paths": ["solution.py"]}
    assert set(projected) == {"task_id", "goal", "inputs", "submission"}
