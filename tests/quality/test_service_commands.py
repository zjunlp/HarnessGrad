"""Service launch overrides, without loading any benchmark task pools."""
from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import pytest

from data.scaffolds import registry
from eval.containers import container
from eval.service import services
from harnessgrad.environments import _env_record, _resolve_envs

ROOT = Path(__file__).resolve().parents[2]
PIN = "sha256:" + "a" * 64


def _service(**overrides):
    return {"name": "api", "image": PIN, "port": 8080,
            "health": {"argv": ["true"], "timeout_s": 1},
            "record": True, **overrides}


def _env(svc):
    return {"kind": "exec", "image": PIN, "network": "services",
            "services": [svc]}


@pytest.mark.parametrize("command", [None, "python3 server.py", [], (), [1],
                                      [""], [" "], ["python3", None],
                                      ["python3", "bad\x00arg"]])
def test_invalid_command_is_refused(command):
    with pytest.raises(ValueError, match="`command`"):
        registry._check_env(_env(_service(command=command)), "t", "ds")


@pytest.mark.parametrize("entrypoint", [None, "", " ", [], ["python3"], 1,
                                         "python3\x00"])
def test_invalid_entrypoint_is_refused(entrypoint):
    with pytest.raises(ValueError, match="`entrypoint`"):
        registry._check_env(_env(_service(entrypoint=entrypoint)), "t", "ds")


def test_overrides_survive_normalization_resolution_and_recording():
    # Whitespace, shell syntax, empty arguments and leading dashes remain literal.
    command = ["-c", "print('$(id) with spaces')", ""]
    spec = _env(_service(command=command, entrypoint="/usr/local/bin/python3"))
    registry._check_env(spec, "t", "ds")
    normalized = registry._normalize_env(spec)
    command.append("later mutation")
    resolved, problem = _resolve_envs({"t": normalized})
    assert problem is None
    recorded = _env_record(resolved, ["t"])["services"][0]
    assert recorded == {"name": "api", "image_digest": PIN,
                        "entrypoint": "/usr/local/bin/python3",
                        "command": ["-c", "print('$(id) with spaces')", ""]}
    assert resolved["t"]["services"][0]["command"] == recorded["command"]


def test_omission_preserves_existing_normalized_and_record_shapes():
    normalized = registry._normalize_env(_env(_service()))
    svc = normalized["services"][0]
    assert "command" not in svc and "entrypoint" not in svc
    assert _env_record({"t": normalized}, ["t"])["services"] == [
        {"name": "api", "image_digest": None}]


@pytest.mark.parametrize("resources, expected", [
    ({}, None),
    ({"cpus": 1.5}, {"cpus": 1.5, "memory": "4096m", "pids": 1024}),
    ({"memory_mb": 256}, {"cpus": 2, "memory": "256m", "pids": 1024}),
    ({"cpus": 3, "memory_mb": 512}, {"cpus": 3, "memory": "512m", "pids": 1024}),
])
def test_service_resources_reach_launch_and_record(tmp_path, monkeypatch, resources, expected):
    spec = _env(_service(**resources))
    # Task caps are deliberately different: they must not leak into service caps.
    spec.update(cpus=8, memory_mb=8192)
    registry._check_env(spec, "t", "ds")
    resolved, problem = _resolve_envs({"t": registry._normalize_env(spec)})
    assert problem is None
    recorded = _env_record(resolved, ["t"])["services"][0]
    assert recorded.get("limits") == expected
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "cid\n" if args[0] == "run" else "", "")

    monkeypatch.setattr(container, "docker", docker)
    running = services.start(resolved["t"]["services"], recordings_root=tmp_path,
                             run_id="caps", task_id="t", run_seed="0")
    cmd = next(call for call in calls if call[0] == "run")
    for flag, key in (("--cpus", "cpus"), ("--memory", "memory"), ("--pids-limit", "pids")):
        if expected is None:
            assert flag not in cmd
        else:
            assert cmd.count(flag) == 1
            assert cmd.index(flag) < cmd.index(PIN)
            assert cmd[cmd.index(flag) + 1] == str(expected[key])
    assert running.stop() == []


@pytest.mark.parametrize("field, bad", [
    (field, bad) for field in ("cpus", "memory_mb")
    for bad in (None, True, 0, -1, "2", float("nan"), float("inf"))
] + [("cpus", 0.5), ("memory_mb", 32)])
def test_invalid_service_resources_are_refused(field, bad):
    with pytest.raises(ValueError, match=field):
        registry._check_env(_env(_service(**{field: bad})), "t", "ds")


@pytest.mark.parametrize("overrides", [{}, {"command": ["/server", "--flag", "a b"]},
                                         {"entrypoint": "/server"},
                                         {"entrypoint": "/server", "command": ["--flag"]}])
def test_start_keeps_launch_arguments_and_privacy_boundaries(tmp_path, monkeypatch, overrides):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        stdout = "cid\n" if args[0] == "run" else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(container, "docker", docker)
    running = services.start([_service(**overrides)], recordings_root=tmp_path,
                             run_id="launch", task_id="t", run_seed="0")
    assert calls[0][:4] == ("network", "create", "--internal", "hg-launch-t")
    cmd = next(call for call in calls if call[0] == "run")
    image_at = cmd.index(PIN)
    assert list(cmd[image_at + 1:]) == overrides.get("command", [])
    if "entrypoint" in overrides:
        assert cmd[image_at - 2:image_at] == ("--entrypoint", overrides["entrypoint"])
    else:
        assert "--entrypoint" not in cmd
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert cmd[cmd.index("--network") + 1] == running.network
    assert "-p" not in cmd and "--publish" not in cmd
    assert cmd[cmd.index("--mount") + 1] == (
        f"type=bind,src={tmp_path / 't/api'},dst=/recordings/api")
    assert services.mounts_for("harness", running.services, running.recordings) == []
    assert services.mounts_for("verifier", running.services, running.recordings)
    assert next(call for call in calls if call[0] == "exec") == ("exec", "cid", "true")
    assert running.stop() == []
    assert calls[-2:] == [("rm", "--force", "cid"), ("network", "rm", running.network)]


def test_failed_explicit_launcher_cleans_up_network(tmp_path, monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, int(args[0] == "run"), "", "not found")

    monkeypatch.setattr(container, "docker", docker)
    with pytest.raises(services.ServiceUnavailable) as exc:
        services.start([_service(command=["/missing-server"])], recordings_root=tmp_path,
                       run_id="failed", task_id="t", run_seed="0")
    assert exc.value.stage == "start" and exc.value.service == "api"
    assert calls[-1] == ("network", "rm", "hg-failed-t")


def test_appworld_declares_absolute_launcher_without_reading_task_pool():
    # Importing data.datasets.appworld reads the full pool; parse only the static declaration.
    tree = ast.parse((ROOT / "data/datasets/appworld.py").read_text())
    node = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "DEFAULT_ENV"
                        for target in node.targets))
    spec = eval(compile(ast.Expression(node), "data/datasets/appworld.py", "eval"),
                {"__builtins__": {}, "IMAGE": PIN, "SERVICE_IMAGE": PIN, "WORKDIR": "/app"})
    registry._check_env(spec, "train-smoke", "appworld")
    svc = registry._normalize_env(spec)["services"][0]
    assert svc["command"] == ["/usr/local/bin/appworld-server"]
    assert svc["record"] is True and spec["network"] == "services"
    assert (svc["cpus"], svc["memory_mb"]) == (spec["cpus"], spec["memory_mb"]) == (2, 4096)
    assert container.limits_from(svc) == container.limits_from(registry.ENV_DEFAULTS)


def test_real_docker_exec_overrides_record_and_keep_invoking_owner(tmp_path):
    image = os.environ.get("HG_EXEC_DEMO_IMAGE", "docker.m.daocloud.io/library/python:3.11-slim")
    if container.available() is None:
        pytest.skip("no docker daemon")
    try:
        pin = container.resolve_digest(image)
    except container.ContainerUnavailable:
        pytest.skip("local Python image unavailable; never pull during tests")
    code = ("from pathlib import Path; import http.server; "
            "Path('/recordings/api/started.json').write_text('{\"started\": true}'); "
            "http.server.HTTPServer(('0.0.0.0', 8080), "
            "http.server.BaseHTTPRequestHandler).serve_forever()")
    svc = _service(image=pin, entrypoint="/usr/local/bin/python3", command=["-u", "-c", code],
                   health={"argv": ["python3", "-c", "import socket; socket.create_connection(('localhost',8080),2).close()"],
                           "timeout_s": 15})
    running = services.start([svc], recordings_root=tmp_path, run_id=f"launch-test-{os.getpid()}",
                             task_id="t", run_seed="0")
    try:
        record = tmp_path / "t/api/started.json"
        assert json.loads(record.read_text()) == {"started": True}
        assert record.stat().st_uid == os.getuid()
        assert record.stat().st_gid == os.getgid()
        assert services.mounts_for("harness", [svc], running.recordings) == []
        inspected = container.docker("inspect", running.containers["api"])
        payload = json.loads(inspected.stdout)[0]
        assert payload["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
        assert payload["Path"] == "/usr/local/bin/python3"
        assert payload["Args"] == ["-u", "-c", code]
        assert payload["HostConfig"]["PortBindings"] == {}
        net = json.loads(container.docker("network", "inspect", running.network).stdout)[0]
        assert net["Internal"] is True
    finally:
        assert running.stop() == []
