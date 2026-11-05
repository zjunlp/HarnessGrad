"""The miniswe harness: somebody else's agent, measured by this platform.

`base_harness/miniswe` wraps mini-SWE-agent, so most of what can go wrong is not the
platform's code but the *translation*: a dependency tree that lives under a home directory
the sandbox replaces, a CLI whose own first-run wizard needs a TTY, a cost book with no
entry for a local model, and a trajectory format that is not this platform's trace. Each of
those failed once while this harness was being written, and each has a test here.

What is *not* tested here is the model. `HG_AGENT_BACKEND=mock` runs a scripted endpoint
(`mock_model.py`) so the contract -- argv, env, cwd, tool-call round trip, trace, answer
file -- is measured with no network and no cost. A mock run's score is not a measurement of
anything, and `harness_identity.agent_backend` says `mock` so nobody can quote it as one.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HARNESS = ROOT / "base_harness" / "miniswe"

needs_miniswe = pytest.mark.skipif(
    importlib.util.find_spec("minisweagent") is None,
    reason="mini-swe-agent is not installed on this machine")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agent():
    return _load("_miniswe_agent", HARNESS / "agent.py")


# ------------------------------------------------------------------ the contract ---

def test_the_manifest_satisfies_the_contributor_contract():
    """`tools/validate_harness.py` is the stated answer to "does my harness conform"."""
    validator = _load("_validate_harness", ROOT / "tools" / "validate_harness.py")
    assert validator.validate(HARNESS, smoke=False) == []


@needs_miniswe
def test_a_mock_run_writes_an_answer_and_a_trace(tmp_path):
    """The whole entrypoint, as the platform invokes it: argv, cwd, exit code, artifacts."""
    from eval.containers import harness_runtime, sandbox

    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    harness = tmp_path / "work/miniswe"
    shutil.copytree(HARNESS, harness, ignore=shutil.ignore_patterns("__pycache__"))
    overlay = harness_runtime.lookup(harness, sys.executable)
    if overlay is None:
        pytest.skip("provision the miniSWE overlay for this Python before the native integration control")
    work = tmp_path / "task"
    work.mkdir()
    (work / "task.json").write_text(json.dumps({
        "task_id": "t-mock", "goal": "Write the word alpha into answer.txt."}))
    env = {**os.environ, **harness_runtime.env_for(overlay), "HG_AGENT_BACKEND": "mock"}
    command = sandbox.command(
        [sys.executable, str(harness / "agent.py"), "--task", str(work / "task.json"),
         "--workdir", str(work)], platform=ROOT, work_root=harness.parent,
        task_dir=work, repo=harness, env=env, network="none",
        runtime_paths=harness_runtime.extra_paths(overlay))
    proc = subprocess.run(
        command, env=env, cwd=work,
        capture_output=True, text=True, errors="replace", timeout=300)
    assert proc.returncode == 0, proc.stderr[-1200:]
    assert (work / "answer.txt").read_text().strip() == "MINISWE_MOCK"

    trace = [json.loads(line) for line in
             (work / "trace.jsonl").read_text().splitlines() if line.strip()]
    identity = trace[0]["harness_identity"]
    assert identity["harness"] == "miniswe"
    assert identity["agent_backend"] == "mock", "a mock run must say so"
    assert trace[0]["task_id"] == "t-mock"
    commands = [row["command"] for row in trace if "command" in row]
    assert len(commands) == 2, commands
    assert commands[0].startswith("printf"), commands
    assert any("usage" in row for row in trace), "the platform counts calls from this"
    final = [row for row in trace if row.get("submitted")][-1]
    assert final["answer_source"] == "artifact", final


# ------------------------------------------------------- what the model is told ---

def test_the_goal_reaches_the_task_text(agent):
    """The adapter carries the task, not a delivery channel.

    `answer.txt` used to be named here for every task. It is now the *task's* requirement
    (`eval/run/task_contract.py:runtime_task` injects it for answer-graded tasks, and the QA
    datasets write their own section), so an adapter that named it again would be deciding
    the channel for datasets whose verifier never reads that file.
    """
    text = agent.task_text({"goal": "Find the lamp.", "task_id": "x"})
    assert "Find the lamp." in text
    assert "answer.txt" not in text
    assert "Write every file the task asks for exactly as asked." in text


def test_a_file_submission_task_is_told_not_to_write_an_answer(agent):
    """Two delivery contracts, and telling a file task to write `answer.txt` would make it
    leave the wrong artifact behind."""
    text = agent.task_text({"goal": "g", "submission": {
        "kind": "files", "paths": ["out/result.json"]}})
    assert "out/result.json" in text
    assert "do not write answer.txt" in text.lower()


@pytest.mark.parametrize("submission", [
    {"kind": "files", "paths": []},
    {"kind": "files", "paths": ["../escape"]},
    {"kind": "files", "paths": ["/abs"]},
    {"kind": "files", "paths": ["a"], "extra": 1},
    {"kind": "other"},
])
def test_an_unsafe_submission_descriptor_is_refused(agent, submission):
    with pytest.raises(ValueError):
        agent.task_text({"goal": "g", "submission": submission})


def test_an_empty_goal_is_refused(agent):
    with pytest.raises(ValueError):
        agent.task_text({"goal": "   "})
    with pytest.raises(ValueError):
        agent.task_text({})


def test_the_model_gets_the_provider_prefix_mini_requires(agent):
    assert agent._model({"HG_AGENT_MODEL": "qwen3.5-9b-local"}, "openai") \
        == "openai/qwen3.5-9b-local"
    assert agent._model({"HG_AGENT_MODEL": "anthropic/claude"}, "openai") == "anthropic/claude"
    assert agent._model({}, "mock") == "openai/miniswe-mock"
    with pytest.raises(SystemExit):
        agent._model({}, "openai")


def test_a_live_backend_without_an_endpoint_is_refused(agent, monkeypatch, tmp_path):
    """No silent default endpoint: a harness that guesses where the model is produces
    measurements of a machine nobody recorded."""
    monkeypatch.setenv("HG_AGENT_BACKEND", "openai")
    monkeypatch.setenv("HG_AGENT_MODEL", "qwen3.5-9b-local")
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    with pytest.raises(SystemExit, match="HG_AGENT_BASE_URL"):
        agent.run({"goal": "g"}, tmp_path / "w")


# --------------------------------------------------------------- the answer ---

def test_the_answer_prefers_the_file_the_run_wrote(agent, tmp_path):
    (tmp_path / "answer.txt").write_text("before\n")
    before = agent._stamp(tmp_path)
    (tmp_path / "answer.txt").write_text("after\n")
    assert agent.save_answer(tmp_path, {}, before, "a final message") == ("after\n", "artifact")


def test_the_final_message_becomes_the_answer_when_nothing_was_written(agent, tmp_path):
    answer, source = agent.save_answer(tmp_path, {}, None, "  42 ")
    assert source == "final_message"
    assert answer.strip() == "42"
    assert (tmp_path / "answer.txt").read_text().strip() == "42"


def test_a_missing_submission_file_is_a_failure_not_a_zero(agent, tmp_path):
    """The score is the platform's; the harness's job is to say the run left nothing --
    otherwise an empty directory reads as "the harness did the task badly"."""
    with pytest.raises(ValueError, match="missing submission"):
        agent.save_answer(tmp_path, {"submission": {"kind": "files", "paths": ["out.json"]}},
                          None, "x")


def test_no_answer_at_all_is_a_failure(agent, tmp_path):
    with pytest.raises(ValueError):
        agent.save_answer(tmp_path, {}, None, "   ")


# ---------------------------------------------------------------- the trace ---

def test_the_trace_keeps_the_commands_and_the_tokens(agent, tmp_path):
    """mini's trajectory is not this platform's trace, and the platform counts `command`
    records: a trace of opaque blobs would report a harness that did nothing while it ran
    ten commands."""
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(json.dumps({
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "", "extra": {
                "actions": [{"command": "ls"}],
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}}},
            {"role": "tool", "content": "{}", "extra": {
                "returncode": 0, "raw_output": "a b", "exception_info": ""}},
            {"role": "assistant", "content": "", "extra": {
                "actions": [{"command": "echo done"}],
                "response": {"usage": {"prompt_tokens": 12, "completion_tokens": 3}}}},
            {"role": "tool", "content": "{}", "extra": {
                "returncode": -1, "raw_output": "", "exception_info": "action was not executed"}},
        ],
        "info": {"model_stats": {"api_calls": 2}, "mini_version": "2.4.6",
                 "exit_status": "Submitted"},
    }))
    rows: list[dict] = []
    summary = agent.write_trace(trajectory, rows.append)
    steps = [row for row in rows if "command" in row]
    assert [row["command"] for row in steps] == ["ls", "echo done"]
    assert steps[1]["error"] == "action was not executed"
    assert summary["steps"] == 2, "two model turns"
    assert summary["actions"] == 2, "two executed actions"
    usage = [row["usage"] for row in rows if "usage" in row][-1]
    assert usage == {"input_tokens": 22, "output_tokens": 5, "calls": 2}


def test_a_missing_trajectory_is_not_an_exception(agent, tmp_path):
    """The CLI can be killed before it saves anything: the trace still has to be written,
    and an empty one is honest where a crash is not."""
    rows: list[dict] = []
    summary = agent.write_trace(tmp_path / "absent.json", rows.append)
    assert summary["steps"] == 0 and rows == []
