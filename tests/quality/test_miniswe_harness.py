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
import tempfile
from pathlib import Path

import pytest
from test_miniswe_managed_runtime import managed_miniswe, miniswe_store  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HARNESS = ROOT / "base_harness" / "miniswe"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agent():
    return _load("_miniswe_agent", HARNESS / "agent.py")


@pytest.fixture
def native_harness(tmp_path):
    harness = tmp_path / "work/miniswe"
    shutil.copytree(HARNESS, harness, ignore=shutil.ignore_patterns("__pycache__"))
    return harness


@pytest.fixture
def legacy_harness(native_harness):
    manifest=native_harness/'harness.json'
    value=json.loads(manifest.read_text());value.pop('runtime')
    value['install']='requirements.txt';manifest.write_text(json.dumps(value))
    return native_harness


def _required_overlay(harness):
    from eval.containers import harness_runtime

    overlay = harness_runtime.lookup(harness, sys.executable)
    if overlay is None:
        pytest.skip("provision the miniSWE overlay for this Python before the native integration control")
    return overlay


def _native_command(harness, work, overlay, env, argv):
    from eval.containers import harness_runtime, sandbox

    return sandbox.command(
        argv, platform=ROOT, work_root=harness.parent, task_dir=work, repo=harness,
        env=env, network="none", runtime_paths=harness_runtime.extra_paths(overlay),
        visible_paths=harness_runtime.surfaced_paths(
            overlay, work_root=harness.parent, platform=ROOT))


# ------------------------------------------------------------------ the contract ---

def test_the_manifest_satisfies_the_contributor_contract():
    """`tools/validate_harness.py` is the stated answer to "does my harness conform"."""
    validator = _load("_validate_harness", ROOT / "tools" / "validate_harness.py")
    assert validator.validate(HARNESS, smoke=False) == []


def test_a_mock_run_writes_an_answer_and_a_trace(tmp_path, native_harness,managed_miniswe):
    """The whole entrypoint, as the platform invokes it: argv, cwd, exit code, artifacts."""
    from harnessgrad import candidate_runtime
    from eval.run import runner

    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    harness = native_harness
    binding=candidate_runtime.prepare(harness)['identity']
    result=runner.run_one(harness,{'task_id':'t-mock','goal':'Write the word alpha into answer.txt.'},
        env={'kind':'files','network':'none'},verifier={'kind':'answer','expected':'MINISWE_MOCK'},
        harness_runtime_identity=binding,timeout_s=90)
    assert not result.get('invalid') and result['exit_code']==0,result
    assert result['answer'].strip()=='MINISWE_MOCK' and result['verdict']['passed']
    trace = [json.loads(line) for line in result['trace'].splitlines() if line.strip()]
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


def test_missing_overlay_skips_legacy_integration_explicitly(legacy_harness, tmp_path, monkeypatch):
    from eval.containers import harness_runtime

    monkeypatch.setattr(harness_runtime, "overlays_root", lambda: tmp_path / "missing-overlays")
    with pytest.raises(pytest.skip.Exception, match="provision the miniSWE overlay for this Python"):
        _required_overlay(legacy_harness)


def test_tmp_overlay_is_surfaced_without_a_host_miniswe_install(legacy_harness, tmp_path, monkeypatch):
    """Exercise the real mount plan with one dependency, without emulating mini's CLI."""
    from eval.containers import harness_runtime
    native_harness=legacy_harness

    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    with tempfile.TemporaryDirectory(prefix="hg-miniswe-overlay-control-", dir="/tmp") as directory:
        monkeypatch.setattr(harness_runtime, "overlays_root", lambda: Path(directory))
        install = harness_runtime.install_spec(native_harness)
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        path = harness_runtime.overlay_path(install, version)
        package = path / "minisweagent"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("SOURCE = 'synthetic-overlay-only'\n")
        (path / "PROVENANCE.json").write_text(json.dumps({
            "python": version, "install_sha256": install[0],
            "packages": {"mini_swe_agent": {"version": "synthetic", "top_level": ["minisweagent"]}}}))
        # Global packages must not determine whether the integration control runs.
        with monkeypatch.context() as host:
            host.setattr(sys, "path", [p for p in sys.path if not (Path(p) / "minisweagent").exists()])
            host.delitem(sys.modules, "minisweagent", raising=False)
            assert importlib.util.find_spec("minisweagent") is None
            overlay = _required_overlay(native_harness)
        assert overlay["path"] == path
        work = tmp_path / "task"
        work.mkdir()
        monkeypatch.delenv("PYTHONPATH", raising=False)
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", **harness_runtime.env_for(overlay)}
        code = """import json, os, runpy, sys
from pathlib import Path
adapter = runpy.run_path(sys.argv[1])
assert adapter['_site_for_child']() is None
scratch = Path('scratch')
scratch.mkdir()
assert adapter['child_environment'](dict(os.environ), scratch)['PYTHONPATH'] == os.environ['PYTHONPATH']
import minisweagent
readonly = False
try:
    Path(minisweagent.__file__).write_text('unexpected mutation')
except OSError:
    readonly = True
print(json.dumps({'source': minisweagent.SOURCE, 'file': minisweagent.__file__, 'readonly': readonly}))
"""
        command = _native_command(native_harness, work, overlay, env,
                                  [sys.executable, "-S", "-c", code, str(native_harness / "agent.py")])
        proc = subprocess.run(command, env=env, cwd=work, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == {
            "source": "synthetic-overlay-only", "file": str(package / "__init__.py"), "readonly": True}


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
