"""Regressions for the AppWorld missing-recording diagnosis; no dataset/model access."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from eval.containers import container
from eval.run import runner
from eval.service import services
from policy_assertions import assert_policy_refusal

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("action", [False, True])
def test_aw_dispatches_init_and_action(tmp_path, monkeypatch, action, capsys):
    loader = importlib.machinery.SourceFileLoader("aw_probe", str(ROOT / "data/datasets/appworld/cli/aw"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    aw = importlib.util.module_from_spec(spec)
    loader.exec_module(aw)
    monkeypatch.chdir(tmp_path)
    calls = []

    def post(path, payload):
        calls.append((path, payload))
        return {"task_id": "train_1"} if path == "/initialize" else "ok"

    monkeypatch.setattr(aw, "post", post)
    argv = ["aw", "action" if action else "init", "--task", "train_1"]
    if action:
        argv += ["--code", "print(1)"]
    monkeypatch.setattr(sys, "argv", argv)
    assert aw.main() == 0
    assert calls[0] == ("/initialize", {"task_id": "train_1", "experiment_name": "harness"})
    assert aw.MARKER.read_text() == "train_1"
    if action:
        assert calls[1] == ("/execute", {"task_id": "train_1", "code": "print(1)"})
    assert capsys.readouterr().out.strip()


def test_runner_mounts_dependency_overlay_once(tmp_path, monkeypatch):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "agent.py"}))
    overlay_path = tmp_path / "overlay"
    overlay_path.mkdir()
    overlay = {"path": overlay_path, "python": "3.11", "install": "requirements.txt",
               "install_sha256": "abc", "provenance": {}}
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    monkeypatch.setattr(runner.container_mod, "image_python", lambda _: "/usr/local/bin/python3")
    monkeypatch.setattr(runner.harness_runtime, "lookup", lambda *a, **kw: overlay)
    monkeypatch.setattr(runner, "_verify", lambda *a, **kw: {"passed": True})
    commands = []

    def launch(argv, **kw):
        # Exercise the actual Docker command builder, including nested mounts.
        cmd = container.command(argv, **{k: v for k, v in kw.items() if k != "timeout_s"})
        destinations = [cmd[i + 1].split("dst=", 1)[1].split(",", 1)[0]
                        for i, value in enumerate(cmd) if value == "--mount"]
        commands.append(cmd)
        assert len(destinations) == len(set(destinations)), "Docker rejects duplicate destinations"
        assert destinations.count(str(overlay_path)) == 1
        assert kw["env"]["PYTHONPATH"].split(":")[0] == str(overlay_path)
        (kw["task_dir"] / "trace.jsonl").write_text('{"actor_started": true}\n')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runner.container_mod, "run", launch)
    env = {"kind": "exec", "image": "sha256:" + "a" * 64}
    refused = runner.run_one(repo, {"task_id": "train_1"}, env=env)
    assert_policy_refusal(refused, ("env.exec_defaults",))
    assert commands == [], "unobserved exec must not reach the mocked backend"
    # Keep the duplicate-mount regression at the mocked backend layer, after pinning
    # real admission. This test-local patch is not a production observer or override.
    monkeypatch.setattr(runner, "_refuse_unobserved_policies", lambda *a, **k: None)
    result = runner.run_one(repo, {"task_id": "train_1"}, env=env)
    assert commands and result["exit_code"] == 0
    assert "actor_started" in result["trace"]


def test_invalid_check_keeps_actor_failure_in_progress(tmp_path, monkeypatch):
    failure = {"task_id": "train_1", "exit_code": 125,
               "stderr": "docker: Duplicate mount point: /overlay",
               "trace": '{"actor_boot": true}\n',
               "verdict": {"unmeasured": "no API-call log"},
               "model_gateway": {"connections": 0}}
    monkeypatch.setattr(runner, "run_one", lambda *a, **kw: failure)
    monkeypatch.setattr(runner, "snapshot", lambda _: {})
    result = runner.evaluate(tmp_path, [{"task_id": "train_1"}], {"train_1": ""},
                             sandbox=False, progress_dir=tmp_path)
    progress = tmp_path / "progress/round-0"
    record = json.loads((progress / "train_1.json").read_text())
    assert record["invalid"]["detail"] == "no API-call log"
    assert record["harness_output"]["exit_code"] == 125
    assert "Duplicate mount point" in record["harness_output"]["stderr"]
    assert (progress / "traces/train_1.invalid.jsonl").read_text() == failure["trace"]
    assert "train_1" in result["invalid"]
    assert "train_1" not in result["per_task"]


@pytest.mark.parametrize("log_failure", [False, True])
def test_service_logs_survive_teardown_and_do_not_block_cleanup(tmp_path, monkeypatch, log_failure):
    commands = []

    def docker(*args, **kw):
        commands.append(args)
        if args[0] == "logs":
            if log_failure:
                raise OSError("logs unavailable")
            return subprocess.CompletedProcess(args, 0, "POST /initialize 500\n", "init failed\n")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(container, "docker", docker)
    running = services.RunningServices(network="owned-net", containers={"appworld": "owned-cid"},
                                       recordings={"appworld": tmp_path}, services=[])
    assert running.stop() == []
    assert commands[0][0] == "logs"
    assert ("rm", "--force", "owned-cid") in commands
    assert ("network", "rm", "owned-net") in commands
    if not log_failure:
        assert "init failed" in (tmp_path / "service.log").read_text()
