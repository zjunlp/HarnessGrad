"""`kind: "exec"` still launches where it always did, and the probe describes *that*.

The reworked commit supplies the missing admission receipt and changes **nothing** about
which container runs the harness: `INTERFACE.md` §2.5.3 puts the harness inside the
environment container (`env.image`), which `container_mod.run` already did, and the service
is a different container reached over the task network. So the first half of this file
pins the launcher — an `exec` environment with one service still goes through
`container_mod.run` on `env.image`, never a `docker exec` into the service container — and
the second half pins that the receipt is derived from that launcher's own flags rather
than from a second, hand-written description of the container.

Measured while writing this: `container_mod.run` takes no `limits`, so the flags
`container_mod.command` applies are its defaults (2 cpus, 4g, 1024 pids) whatever the
environment declares. AppWorld's declared 2 cpus / 4096 MB coincide with them, which is
the coincidence the receipt is allowed to rest on — so it is *checked*, per environment,
instead of assumed. An environment declaring anything else keeps its refusal.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import eval.containers.container as container_mod
import eval.containers.harness_runtime as harness_runtime
import eval.containers.sandbox as sandbox_mod
import eval.run.runner as runner
import eval.service.services as services

AGENT = "harnessgrad/appworld-agent@sha256:" + "a" * 64
SERVICE = "harnessgrad/appworld-service@sha256:" + "d" * 64


def _service(**overrides) -> dict:
    spec = {"name": "appworld", "image": SERVICE, "port": 8000,
            "health": {"argv": ["true"], "timeout_s": 5}, "record": True}
    spec.update(overrides)
    return spec


def _env(**overrides) -> dict:
    spec = {"kind": "exec", "image": AGENT, "workdir": "/appworld", "state": "mount",
            "network": "services", "placement": "inside", "services": [_service()],
            "cpus": 2, "memory_mb": 4096}
    spec.update(overrides)
    return spec


class _FakeRunning:
    def __init__(self, specs):
        self.network = "hg-run-task"
        self.containers = {spec["name"]: "cid-" + spec["name"] for spec in specs}
        self.recordings: dict[str, Path] = {}
        self.services = specs

    def stop(self):
        return []

    def usage(self):
        return {}


@pytest.fixture()
def launched(monkeypatch, tmp_path):
    """Everything `_run_one` needs except a daemon, and a record of what it launched."""
    calls: dict[str, list] = {"container_run": [], "sandbox_run": [], "services_start": [],
                              "docker": []}
    monkeypatch.setattr(container_mod, "image_python", lambda image: "/usr/bin/python3")
    monkeypatch.setattr(harness_runtime, "lookup", lambda *a, **k: None)

    def fake_start(specs, **kwargs):
        calls["services_start"].append({"specs": specs, **kwargs})
        return _FakeRunning(specs)

    monkeypatch.setattr(services, "start", fake_start)

    def fake_container_run(argv, **kwargs):
        calls["container_run"].append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(container_mod, "run", fake_container_run)

    def fake_sandbox_run(argv, **kwargs):
        calls["sandbox_run"].append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sandbox_mod, "run", fake_sandbox_run)
    # Any *other* docker invocation (a `docker exec`, a `docker run`) is recorded rather
    # than executed, so a launcher that was not supposed to exist would show up here.
    monkeypatch.setattr(container_mod, "docker",
                        lambda *a, timeout=120: (calls["docker"].append(list(a)),
                                                 subprocess.CompletedProcess(a, 0, "", ""))[1])
    monkeypatch.setattr(runner, "_verify",
                        lambda *a, **k: {"kind": "command", "passed": True, "score": 1.0})
    return calls


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "workspace"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps(
        {"name": "fake", "entrypoint": "agent.py", "env_kinds": ["exec", "files"],
         "runtime_paths": []}))
    (repo / "agent.py").write_text("# a harness\n")
    return repo


def _run(repo: Path, env: dict) -> dict:
    return runner._run_one(repo, {"task_id": "t1", "goal": "g"}, timeout_s=60,
                           sandbox=True, setup=None, verifier={"kind": "command"},
                           verify_timeout_s=60, env=env, run_id="run", run_seed="0",
                           recordings_root=None)


def test_an_exec_environment_with_a_service_still_runs_on_its_own_image(launched, tmp_path):
    """The pin: the environment container, not the service container."""
    repo = _repo(tmp_path)
    _run(repo, _env())
    assert len(launched["container_run"]) == 1, "the environment launcher was not used"
    assert not launched["sandbox_run"]
    call = launched["container_run"][0]
    assert call["image"] == AGENT, "the harness must run in env.image, not a service image"
    # The container path's own translation, unchanged: the task is at the declared workdir
    # inside the container, and the harness is told that path.
    assert call["task_mount"] == "/appworld"
    assert call["cwd"] == "/appworld"
    assert not any(args[:1] == ["exec"] for args in launched["docker"]), (
        "no `docker exec` may be used to reach any container here")


def test_the_service_container_is_never_given_the_task_tree(launched, tmp_path):
    """`services.start` keeps the arguments it had; no host tree is added to a service."""
    repo = _repo(tmp_path)
    _run(repo, _env())
    start = launched["services_start"][0]
    assert set(start) == {"specs", "recordings_root", "run_id", "task_id", "run_seed"}


def test_a_files_environment_still_uses_the_host_sandbox(launched, tmp_path):
    repo = _repo(tmp_path)
    env = {"kind": "files", "network": "none"}
    _run(repo, env)
    assert len(launched["sandbox_run"]) == 1
    assert not launched["container_run"]
    argv = sandbox_mod.command(launched["sandbox_run"][0]["argv"],
                               platform=runner.PLATFORM_ROOT, work_root=tmp_path,
                               task_dir=launched["sandbox_run"][0]["task_dir"], env={})
    assert "bwrap" in argv[0]
    assert "docker" not in argv


# ------------------------------------------------- the probe is the same container ---

def test_the_probe_spec_is_the_launcher_envelope_not_the_declaration():
    """The receipt describes the flags the container gets, derived from `limits_from`."""
    env = _env(cpus=4, memory_mb=8192)
    shape = runner._environment_shape(env)
    applied = container_mod.envelope(container_mod.limits_from(env))
    assert (shape["cpus"], shape["memory_mb"]) == (applied["cpus"], applied["memory_mb"])
    assert (shape["cpus"], shape["memory_mb"]) == (4.0, 8192)
    assert shape["image"] == AGENT
    assert runner._declared_but_not_applied(env, shape) is None, (
        "a launcher that was given the declaration must not trip the guard")
    # And the image the launcher starts is the one probed, not the service's.
    assert shape["image"] != SERVICE


def test_the_guard_still_trips_on_a_declaration_the_launcher_cannot_express():
    """`limits_from` truncates `memory_mb`, so 4096.5 MB is applied as 4096.

    That is the one path left where the declaration does not survive to the container, and
    a task on it keeps its refusal instead of getting a receipt for a bound it did not get.
    """
    env = _env(memory_mb=4096.5)
    shape = runner._environment_shape(env)
    assert shape["memory_mb"] == 4096
    problem = runner._declared_but_not_applied(env, shape)
    assert problem and "4096.5" in problem and "4096" in problem


def test_a_matching_declaration_is_admitted_by_the_shape_alone():
    shape = runner._environment_shape(_env())
    assert runner._declared_but_not_applied(_env(), shape) is None


def test_the_declared_defaults_are_what_the_launcher_applies():
    """A dataset that declares nothing gets the registry's defaults -- the same numbers."""
    env = {key: value for key, value in _env().items()
           if key not in ("cpus", "memory_mb")}
    shape = runner._environment_shape(env)
    assert (shape["cpus"], shape["memory_mb"]) == (2.0, 4096)
    assert runner._declared_but_not_applied(env, shape) is None


def test_the_envelope_is_the_only_place_the_flags_come_from(tmp_path):
    """`command()` writes `envelope()`; a second copy of the defaults is what drifts."""
    argv = container_mod.command(["true"], image=AGENT, task_dir=tmp_path,
                                task_mount="/app")
    for flag, value in (("--cpus", "2"), ("--memory", "4g"), ("--pids-limit", "1024")):
        assert argv[argv.index(flag) + 1] == value, argv
    declared = container_mod.limits_from({"cpus": 4, "memory_mb": 2048})
    argv = container_mod.command(["true"], image=AGENT, task_dir=tmp_path,
                                task_mount="/app", limits=declared)
    for flag, value in (("--cpus", "4"), ("--memory", "2048m"), ("--pids-limit", "1024")):
        assert argv[argv.index(flag) + 1] == value, argv
    assert container_mod.envelope(declared)["memory_mb"] == 2048


def test_container_state_environments_are_not_probed_yet(monkeypatch):
    """That launcher has a different envelope and network; a probe of this shape would be
    a receipt for a container nobody starts. It keeps today's refusal."""
    shape = runner._environment_shape(_env(state="container"))
    assert shape is None
    assert runner._observations_for({"a": _env(state="container")},
                                    [{"task_id": "a"}], run_id="r") == {}


# ------------------------------------------- the declaration reaches the launcher ---

def _flags(argv: list[str]) -> list[str]:
    """The resource flags, in the order `command()` writes them."""
    return [argv[argv.index(flag)] + " " + argv[argv.index(flag) + 1]
            for flag in ("--pids-limit", "--memory", "--cpus")]


#: What `container_mod.command` emitted before the declaration was forwarded. Both
#: datasets on the `state: "mount"` exec path declare this envelope (appworld and
#: tau_bench: `cpus: 2, memory_mb: 4096`), and the whole point of the default spelling is
#: that the argv those two runs produce does not change.
_HEAD_FLAGS = ["--pids-limit 1024", "--memory 4g", "--cpus 2"]


@pytest.mark.parametrize("workdir", ["/appworld", "/app"])
def test_a_declared_envelope_produces_the_argv_it_produced_before(tmp_path, workdir):
    """appworld- and tau_bench-shaped declarations: byte-identical flags, `2` not `2.0`."""
    declared = container_mod.limits_from({"cpus": 2, "memory_mb": 4096})
    forwarded = container_mod.command(["true"], image=AGENT, task_dir=tmp_path,
                                      task_mount=workdir, limits=declared)
    before = container_mod.command(["true"], image=AGENT, task_dir=tmp_path,
                                   task_mount=workdir)
    assert forwarded == before, "a declaration that states the default changed the argv"
    assert _flags(forwarded) == _HEAD_FLAGS
    assert "2.0" not in forwarded


def test_run_without_limits_is_the_argv_it_was(tmp_path, monkeypatch):
    """`limits=None` is what the verifier passes, and it must not move."""
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        monkeypatch.setattr(container_mod, "remove_container", lambda name: None)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(container_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(container_mod, "remove_container", lambda name: None)
    container_mod.run(["true"], image=AGENT, task_dir=tmp_path, task_mount="/app")
    assert _flags(captured[0]) == _HEAD_FLAGS


def test_a_wider_declaration_reaches_the_flags(tmp_path):
    """The defect this commit closes: `cpus: 4, memory_mb: 8192` used to run at 2 / 4g."""
    argv = container_mod.command(
        ["true"], image=AGENT, task_dir=tmp_path, task_mount="/app",
        limits=container_mod.limits_from({"cpus": 4, "memory_mb": 8192}))
    assert _flags(argv) == ["--pids-limit 1024", "--memory 8192m", "--cpus 4"]
    applied = container_mod.envelope(container_mod.limits_from(
        {"cpus": 4, "memory_mb": 8192}))
    assert (applied["cpus"], applied["memory_mb"]) == (4.0, 8192)


def test_the_runner_passes_the_declaration(launched, tmp_path):
    """The call site, not just the plumbing underneath it."""
    repo = _repo(tmp_path)
    _run(repo, _env(cpus=4, memory_mb=8192))
    call = launched["container_run"][0]
    assert call["limits"] == container_mod.limits_from({"cpus": 4, "memory_mb": 8192}), (
        "the runner must hand the launcher the declared envelope")


def test_the_verifier_call_site_still_gets_no_envelope():
    """Its container is a different contract; forwarding the harness's envelope into it
    would change the check's resources in a commit about admission."""
    import ast
    import inspect

    from eval.verification import verifier

    tree = ast.parse(inspect.getsource(verifier))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "run"
             and isinstance(node.func.value, ast.Name)
             and node.func.value.id == "container_mod"]
    assert calls, "the verifier's container launch moved; update this test"
    for call in calls:
        keywords = {keyword.arg for keyword in call.keywords}
        assert "limits" not in keywords, (
            "the verifier's fresh container must keep `command()`'s default envelope")
