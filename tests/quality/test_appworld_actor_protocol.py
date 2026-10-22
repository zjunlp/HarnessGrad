"""Synthetic actor/tool contract checks; no AppWorld data, Docker or model calls."""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from data.scaffolds import registry
from eval.containers import container_state
from eval.run import runner
from eval.run.task_contract import runtime_task

ROOT = Path(__file__).resolve().parents[2]


def adapter_contract():
    # Extract public helpers only: importing data.datasets.appworld would read the mixed pool.
    tree = ast.parse((ROOT / "data/datasets/appworld.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in {"_goal", "_runtime_prompt", "_setup"}]
    namespace = {"shlex": shlex, "ASSETS": ROOT / "data/datasets/appworld"}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "adapter_contract", "exec"),
         namespace)
    return namespace


@pytest.mark.parametrize("bad", [None, "", "  \n", 2, [], {}])
def test_invalid_runtime_prompt_is_rejected(bad):
    with pytest.raises(ValueError, match="runtime_prompt"):
        registry._check_setup({"runtime_prompt": bad}, "synthetic", "fixture")
    with pytest.raises(ValueError, match="runtime_prompt"):
        runtime_task({"goal": "Do something"}, {"runtime_prompt": bad})


def test_public_prompt_crosses_boundary_without_source_paths_or_verifier_metadata():
    task = {"task_id": "opaque", "goal": "Do something", "inputs": {"x": "y"}}
    setup = {"files": [{"path": "client", "from": "/private/fixture-source"}],
             "runtime_prompt": "Use ./client action", "verifier": "private-grade"}
    registry._check_setup(setup, "opaque", "fixture")
    rendered = runtime_task(task, setup)
    assert rendered["goal"] == "Do something\n\nRuntime tool instructions:\nUse ./client action"
    assert rendered.keys() == task.keys()
    assert task["goal"] == "Do something"
    assert "private" not in json.dumps(rendered)
    assert runtime_task(task, None) == task
    assert runtime_task(task, {"files": []}) == task


def test_appworld_contract_is_task_independent_and_shell_quotes_opaque_ids():
    contract = adapter_contract()
    task_id = "opaque'; $(touch forbidden)"
    row = {"task_id": task_id, "instruction": "A synthetic request."}
    assert contract["_goal"](row) == row["instruction"]
    setup = contract["_setup"](task_id)
    init = re.search(r"Initialize once: `([^`]+)`", setup["runtime_prompt"]).group(1)
    assert shlex.split(init) == ["./aw", "init", "--task", task_id]
    prompt_a = contract["_runtime_prompt"]("synthetic-a")
    prompt_b = contract["_runtime_prompt"]("synthetic-b")
    assert prompt_a.replace("synthetic-a", "synthetic-b") == prompt_b
    assert "apis.api_docs.show_app_descriptions()" in prompt_a
    assert "show_api_descriptions" in prompt_a and "show_api_doc" in prompt_a
    assert "/recordings/" not in prompt_a
    assert "multiple API calls" in prompt_a
    assert "one API call per action" not in prompt_a


@pytest.mark.parametrize("environment", ["files", "exec-mount", "exec-container"])
def test_all_runtime_paths_deliver_prompt_to_goal_only_harness(tmp_path, monkeypatch, environment):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "agent.py"}))
    (repo / "agent.py").write_text(
        "import argparse,json,pathlib\n"
        "p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir')\n"
        "a=p.parse_args();t=json.loads(pathlib.Path(a.task).read_text())\n"
        "pathlib.Path(a.workdir,'answer.txt').write_text(t['goal'])\n")
    task = {"task_id": "synthetic", "goal": "Original goal"}
    setup = {"runtime_prompt": "Use ./client action", "hidden_metadata": "SECRET"}
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    monkeypatch.setattr(runner.harness_runtime, "lookup", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_verify", lambda *a, **kw: {"passed": True})
    monkeypatch.setattr(runner.container_mod, "image_python", lambda _: sys.executable)
    received = []

    def fake_mount(argv, **kwargs):
        received.append(json.loads((kwargs["task_dir"] / "task.json").read_text()))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_cp_in(name, source, dest, **kwargs):
        if Path(source).name == "task.json":
            received.append(json.loads(Path(source).read_text()))

    if environment == "exec-container":
        c = container_state.container_mod
        for name in ("create", "start", "mkdir_in", "stop", "remove_image", "remove_container"):
            monkeypatch.setattr(c, name, lambda *a, **kw: None)
        monkeypatch.setattr(c, "cp_in", fake_cp_in)
        monkeypatch.setattr(c, "cp_out", lambda *a, **kw: False)
        monkeypatch.setattr(c, "commit", lambda *a, **kw: "synthetic-snapshot")
        monkeypatch.setattr(c, "exec_in", lambda *a, **kw: subprocess.CompletedProcess([], 0, "", ""))
        monkeypatch.setattr(container_state.services_mod, "create_network", lambda *a: "synthetic-network")
        monkeypatch.setattr(container_state.services_mod, "remove_network", lambda *a: None)
        monkeypatch.setattr(container_state, "_verify_container_state", lambda *a, **kw: {"passed": True})
    elif environment == "exec-mount":
        monkeypatch.setattr(runner.container_mod, "run", fake_mount)

    env = ({"kind": "files"} if environment == "files" else
           {"kind": "exec", "image": "synthetic-image", "state":
            "container" if environment == "exec-container" else "mount"})
    result = runner.run_one(repo, task, setup=setup, env=env, sandbox=False)
    if environment == "files":
        assert result["exit_code"] == 0
        expected = runtime_task(task, setup)
        assert result["answer"] == expected["goal"]
    else:
        assert result["exit_code"] is None
        assert result["invalid"]["stage"] == "admission"
        assert result["invalid"]["detail"] == "backend_observation_unavailable"
    expected = runtime_task(task, setup)
    if environment != "files":
        assert received == []
    assert task == {"task_id": "synthetic", "goal": "Original goal"}
    assert "SECRET" not in json.dumps(expected)


@pytest.fixture
def synthetic_service(monkeypatch):
    calls, transport = [], []
    namespace = {}
    state = {"value": None}

    def descriptions():
        calls.append("api_docs.app_descriptions")
        return [{"name": "synthetic", "description": "Synthetic state"}]

    def doc(app_name, api_name):
        calls.append("api_docs.api_doc")
        assert app_name == "synthetic" and api_name == "set_value"
        return {"parameters": [{"name": "value", "type": "string"}]}

    def set_value(value):
        calls.append("synthetic.set_value")
        state["value"] = value
        return {"value": value}

    apis = SimpleNamespace(
        api_docs=SimpleNamespace(show_app_descriptions=descriptions, show_api_doc=doc),
        synthetic=SimpleNamespace(set_value=set_value))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            transport.append((self.path, payload))
            if self.path == "/initialize":
                namespace.clear()
                namespace["apis"] = apis
                output = {"task_id": payload["task_id"], "supervisor": {"email": "synthetic@example.test"}}
            else:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    exec(payload["code"], namespace)
                output = buffer.getvalue()
            body = json.dumps({"output": output}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HG_SVC_APPWORLD_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield calls, transport, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unchanged_loop_discovers_cli_and_executes_api_with_persistent_state(
        tmp_path, monkeypatch, synthetic_service):
    contract = adapter_contract()
    setup = contract["_setup"]("synthetic")
    shutil.copy2(setup["files"][0]["from"], tmp_path / "aw")
    task = runtime_task({"task_id": "synthetic", "goal": "Update synthetic state."}, setup)
    spec = importlib.util.spec_from_file_location("synthetic_loop", ROOT / "base_harness/loop/agent.py")
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    replies = []

    def complete(messages):
        goal = json.loads(messages[1]["content"])["goal"]
        step = len(replies)
        if step == 0:
            command = re.search(r"Initialize once: `([^`]+)`", goal).group(1)
        elif step == 1:
            command = re.search(r"executable API action: `([^`]+)`", goal).group(1)
        elif step == 2:
            assert "synthetic" in messages[-1]["content"]
            code = 'value = "updated"; print(apis.api_docs.show_api_doc(app_name="synthetic", api_name="set_value"))'
            command = "./aw action --task synthetic --code " + shlex.quote(code)
        elif step == 3:
            assert '"value"' in messages[-1]["content"] or "'value'" in messages[-1]["content"]
            command = "./aw action --task synthetic --code 'print(apis.synthetic.set_value(value=value))'"
        else:
            reply = json.dumps({"answer": "done"})
            replies.append(reply)
            return reply
        reply = json.dumps({"tool": "bash", "command": command})
        replies.append(reply)
        return reply

    monkeypatch.setattr(agent, "_complete", complete)
    monkeypatch.setattr(agent, "MAX_STEPS", 8)
    monkeypatch.setattr(agent, "FINISH_NUDGE_STEPS", 0)
    assert agent._missing_deliverables(task, tmp_path) == []
    assert agent.run(task, tmp_path) == "done"
    calls, transport, state = synthetic_service
    assert calls == ["api_docs.app_descriptions", "api_docs.api_doc", "synthetic.set_value"]
    assert [path for path, payload in transport] == ["/initialize", "/execute", "/execute", "/execute"]
    assert state["value"] == "updated"
    assert len(replies) == 5
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert all(e["exit"] == 0 for e in events if "command" in e)
    assert not any(e.get("submit_rejected") for e in events)


@pytest.mark.parametrize("args", [["--help"], ["action", "--help"]])
def test_cli_help_explains_remote_namespace_without_initializing_world(args, synthetic_service):
    result = subprocess.run([sys.executable, str(ROOT / "data/datasets/appworld/cli/aw"), *args],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0
    assert "apis" in result.stdout and "service" in result.stdout
    assert synthetic_service[1] == []


def test_one_action_forwards_multiple_api_calls_and_control_flow(
        tmp_path, synthetic_service):
    cli = ROOT / "data/datasets/appworld/cli/aw"
    code = ('for value in ["first", "second"]:\n'
            '    if value:\n'
            '        print(apis.synthetic.set_value(value=value))\n'
            'saved = value\n')
    first = subprocess.run([sys.executable, str(cli), "action", "--task", "synthetic",
                            "--code", code], cwd=tmp_path,
                           capture_output=True, text=True, timeout=10)
    second = subprocess.run([sys.executable, str(cli), "action", "--task", "synthetic",
                             "--code", 'print(apis.synthetic.set_value(value=saved + "-next"))'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert first.returncode == second.returncode == 0
    calls, transport, state = synthetic_service
    assert calls == ["synthetic.set_value"] * 3
    assert [path for path, _ in transport] == ["/initialize", "/execute", "/execute"]
    assert transport[1][1]["code"] == code
    assert "first" in first.stdout and "second" in first.stdout
    assert state["value"] == "second-next"
