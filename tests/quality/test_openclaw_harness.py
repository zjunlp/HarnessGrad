"""Contract checks for the OpenClaw CLI adapter.

The model-free test drives the installed native CLI through the adapter's local SSE
endpoint. It therefore checks the process/config/cwd/answer/trace translation without a
network request or a paid model.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "base_harness" / "openclaw"


def _run(tmp_path: Path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id": "mock", "goal": "Write the mock answer."}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENCLAW_", "CLAWDBOT_", "PI_"))}
    env["HG_AGENT_BACKEND"] = "mock"
    return subprocess.run([sys.executable, str(HARNESS / "agent.py"), "--task", str(task),
                           "--workdir", str(tmp_path)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, errors="replace", timeout=90)


def test_manifest_is_admitted():
    from tools import validate_harness
    assert validate_harness.validate(HARNESS, smoke=False) == []


def test_native_cli_mock_writes_answer_and_trace(tmp_path):
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (tmp_path / "answer.txt").read_text(encoding="utf-8").strip() == "OPENCLAW_MOCK"
    rows = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["harness_identity"]["harness"] == "openclaw"
    assert rows[0]["harness_identity"]["agent_backend"] == "mock"
    assert sum("reply" in row for row in rows) >= 2
    actions = [row for row in rows if "command" in row]
    assert len(actions) == 1
    assert actions[0]["observation"]["returncode"] == 0
    assert rows[-2]["usage"]["calls"] >= 2
    assert rows[-1]["submitted"] is True
    assert rows[-1]["answer_source"] == "artifact"
    check = subprocess.run([sys.executable, str(ROOT / "tools/check_trace_contract.py"),
                            str(tmp_path / "trace.jsonl")], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    assert "OK" in check.stdout


def test_file_submission_is_kept_in_place(tmp_path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id": "mock-files", "goal": "Write the mock answer.",
                                "submission": {"kind": "files", "paths": ["answer.txt"]}}))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENCLAW_", "CLAWDBOT_", "PI_"))}
    env["HG_AGENT_BACKEND"] = "mock"
    proc = subprocess.run([sys.executable, str(HARNESS / "agent.py"), "--task", str(task),
                           "--workdir", str(tmp_path)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, errors="replace", timeout=90)
    assert proc.returncode == 0, proc.stderr[-2000:]
    final = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines() if line.strip()][-1]
    assert final["answer_source"] == "files"


def _agent():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_test_openclaw_adapter", HARNESS / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_uses_one_platform_endpoint_and_model(tmp_path):
    agent = _agent()
    config = agent.configuration({"HG_AGENT_API_KEY": "task-secret", "HG_AGENT_MAX_OUTPUT_TOKENS": "512",
                                  "HG_AGENT_CONTEXT_WINDOW": "16384"},
                                 "http://127.0.0.1:9999/v1", "test-model", tmp_path, 30)
    providers = config["models"]["providers"]
    assert list(providers) == ["harnessgrad"]
    assert providers["harnessgrad"]["baseUrl"] == "http://127.0.0.1:9999/v1"
    assert providers["harnessgrad"]["models"][0]["id"] == "test-model"
    assert providers["harnessgrad"]["models"][0]["maxTokens"] == 512
    assert config["agents"]["defaults"]["model"] == {"primary": "harnessgrad/test-model", "fallbacks": []}
    assert config["agents"]["defaults"]["workspace"] == str(tmp_path)


def test_task_deadline_is_distinct_from_provider_timeout():
    agent = _agent()
    assert agent.timeout_seconds({"HG_AGENT_TIMEOUT_S": "2"}) == 300
    assert agent.timeout_seconds({"HG_AGENT_OPENCLAW_TIMEOUT_S": "300", "HG_AGENT_DEADLINE_S": "20"}) == 16
    assert agent.timeout_seconds({"HG_AGENT_OPENCLAW_TIMEOUT_S": "3", "HG_AGENT_DEADLINE_S": "20"}) == 3


def test_environment_does_not_import_host_settings_or_improver_credentials(tmp_path):
    agent = _agent()
    parent = {"OPENCLAW_CONFIG_PATH": "/host/private.json", "OPENCLAW_STATE_DIR": "/host/state",
              "HOME": "/host/home", "DEEPSEEK_API_KEY": "do-not-pass", "HG_METHOD_MODEL": "improver",
              "HG_ENV_API_KEY": "environment", "HG_AGENT_API_KEY": "task-secret", "NODE_OPTIONS": "--require=/bad.js"}
    child = agent.child_environment(parent, tmp_path, tmp_path / "cfg", tmp_path / "task")
    assert child["HOME"] == str(tmp_path / "home")
    assert child["OPENCLAW_STATE_DIR"] == str(tmp_path)
    assert child["HG_AGENT_API_KEY"] == "task-secret"
    assert "HG_ENV_API_KEY" not in child and "HG_METHOD_MODEL" not in child
    assert "DEEPSEEK_API_KEY" not in child and "NODE_OPTIONS" not in child


def test_instruction_only_task_is_valid():
    assert "Instruction fallback" in _agent().task_prompt({"instruction": "Instruction fallback"})


@pytest.mark.parametrize("paths", [["../outside"], ["/tmp/outside"], ["sub//file"], [], ["C:\\outside"]])
def test_bad_submission_paths_are_refused(paths):
    with pytest.raises(ValueError):
        _agent().submission_paths({"submission": {"kind": "files", "paths": paths}})


def test_symlink_answer_is_refused_before_read_or_write(tmp_path):
    agent = _agent()
    work = tmp_path / "task"
    work.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("do not change")
    (work / "answer.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="owned regular file"):
        agent._answer_bytes(work)
    with pytest.raises(ValueError, match="owned regular file"):
        agent.save_answer(work, {}, None, "replacement")
    assert outside.read_text() == "do not change"


def test_trace_keeps_action_arguments_output_and_partial_usage_unknown():
    agent = _agent()
    rows = [
        {"message": {"role": "assistant", "content": [{"type": "toolCall", "id": "a", "name": "write",
                     "arguments": {"path": "note.txt", "content": "hello"}}],
                     "usage": {"input": 10, "output": 2, "cacheRead": 4, "cacheWrite": 0}}},
        {"message": {"role": "toolResult", "toolCallId": "a", "toolName": "write",
                     "content": [{"type": "text", "text": "Wrote five bytes."}], "isError": False}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
                     "usage": {"input": 11, "cacheRead": 2, "cacheWrite": 0}}},
    ]
    out = []
    agent.translate_trace(rows, out.append, backend="openai")
    action = [row for row in out if "command" in row][0]
    assert action["command"].startswith("write ")
    assert action["tool_args"]["content"] == "hello"
    assert action["observation"]["output"] == "Wrote five bytes."
    assert action["observation"]["returncode"] is None
    usage = out[-1]["usage"]
    assert usage["calls"] == 2 and usage["input_tokens"] == 21
    assert usage["output_tokens"] is None
    assert usage["cache_read_tokens"] == 6


def test_real_zero_usage_is_not_asserted_to_be_free():
    agent = _agent()
    rows = [{"message": {"role": "assistant", "content": [], "usage": {"input": 0, "output": 0}}}]
    out = []
    agent.translate_trace(rows, out.append, backend="openai")
    assert out[-1]["usage"]["input_tokens"] is None
    mock = []
    agent.translate_trace(rows, mock.append, backend="mock")
    assert mock[-1]["usage"]["input_tokens"] == 0


def test_redaction_removes_credentials_from_nested_native_events():
    data = {"error": "provider rejected a-task-secret-value", "items": [{"output": "a-task-secret-value"}]}
    result = _agent().redact(data, {"HG_AGENT_API_KEY": "a-task-secret-value"})
    assert "a-task-secret-value" not in json.dumps(result)
    assert result["items"][0]["output"] == "<redacted>"
