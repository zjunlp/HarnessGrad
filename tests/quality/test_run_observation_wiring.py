"""The admission receipt: probed once per container shape, and never fabricated.

`_refuse_unobserved_policies` refuses `env.cpus`, `env.memory_mb`, `env.exec_defaults` and
`env.network` unless the caller shows a receipt from
`eval/service/container_observation.py` whose `covers` names every declared field. Nothing
in production supplied one, so AppWorld stopped at `backend_observation_unavailable` and
never ran. These tests fix the wiring and, more importantly, the *boundary* of it:

* the probed shape is the container the launcher starts (`env.image`, and the bounds
  `container_mod.run` really gives it), not a hand-written second description;
* one probe per distinct shape, cached for the process -- a probe starts a container and
  costs about 1.3 s, and a 585-task dataset must not pay it 585 times;
* the receipt reaches `run_one` for **every** task that uses it, and the earlier admission
  check inside `evaluate` uses the same receipt (a receipt that admits a task there and is
  dropped one call later is the same refusal);
* a probe that fails, or that reports `ok: False`, admits **nothing**. "I could not check"
  must never be spelled "ok", and that is the property most worth pinning.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import eval.run.runner as runner
import eval.service.container_observation as co

AGENT = "harnessgrad/appworld-agent@sha256:" + "a" * 64
SERVICE = "harnessgrad/appworld-service@sha256:" + "d" * 64


def _env(**overrides) -> dict:
    spec = {"kind": "exec", "image": AGENT, "workdir": "/appworld", "state": "mount",
            "network": "services", "placement": "inside", "cpus": 2, "memory_mb": 4096,
            "services": [{"name": "appworld", "image": SERVICE, "port": 8000,
                          "health": {"argv": ["true"], "timeout_s": 5}, "record": True}]}
    spec.update(overrides)
    return spec


def _tasks(*ids: str) -> list[dict]:
    return [{"task_id": tid, "goal": "g"} for tid in ids]


def _receipt(image: str = AGENT, spec: dict | None = None) -> dict:
    """What `observe` returns for `spec`: `declared` echoes the envelope it was asked for."""
    spec = dict(spec or {"cpus": 2.0, "memory_mb": 4096})
    return {"schema": co.SCHEMA, "image": image, "image_id": "sha256:" + "e" * 64,
            "declared": spec, "observed": {"cpus_docker": spec["cpus"]},
            "ok": True, "problems": [], "unobserved": [], "covers": list(co.COVERS),
            "probe_seconds": 1.3}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """The probe cache is process-wide; a test must not inherit another test's receipt."""
    monkeypatch.setattr(runner, "_OBSERVATIONS", {})


def _probe(monkeypatch, receipts: dict[str, dict] | None = None, *, raises=None):
    calls: list[dict] = []

    def fake_observe_for(image, spec, *, network, run_id="", read_only=True):
        calls.append({"image": image, "spec": dict(spec), "network": network,
                      "run_id": run_id, "read_only": read_only})
        if raises is not None:
            raise raises
        chosen = (receipts or {}).get(image) or _receipt(image, spec)
        return SimpleNamespace(receipt=lambda: dict(chosen))

    monkeypatch.setattr(co, "observe_for", fake_observe_for)
    return calls


# ------------------------------------------------------------- one probe per shape ---

def test_one_probe_per_distinct_container_shape(monkeypatch):
    other = "harnessgrad/tau-env@sha256:" + "f" * 64
    envs = {"a": _env(), "b": _env(), "c": _env(image=other)}
    calls = _probe(monkeypatch)
    observations = runner._observations_for(envs, _tasks("a", "b", "c"), run_id="run-1")
    assert len(calls) == 2, [c["image"] for c in calls]
    assert {c["image"] for c in calls} == {AGENT, other}, (
        "the probe must measure env.image -- the container the launcher starts")
    assert set(observations) == {"a", "b", "c"}
    assert observations["a"] is observations["b"], "two tasks of one shape share one receipt"
    assert calls[0]["network"] == "services"
    # Derived from the launcher (`container_mod.envelope(None)`), not from the declaration.
    assert calls[0]["spec"] == {"cpus": 2.0, "memory_mb": 4096}
    assert calls[0]["read_only"] is True, "container_mod.run starts a read-only rootfs"


def test_the_probe_is_cached_across_rounds(monkeypatch):
    calls = _probe(monkeypatch)
    for _ in range(3):
        runner._observations_for({"a": _env()}, _tasks("a"), run_id="run-1")
    assert len(calls) == 1


def test_only_a_service_environment_of_the_launched_state_is_probed(monkeypatch):
    """The scope of the receipt is the declaration that was blocked: a named service.

    A plain `exec` environment and a `state: "container"` one are refused today for the
    same "nobody looked" reason, and they keep it -- widening the receipt to them would
    admit datasets nobody has measured, which is a separate change.
    """
    calls = _probe(monkeypatch)
    envs = {"files": {"kind": "files", "network": "none"},
            "container_state": _env(state="container"),
            "exec_no_service": _env(services=[], network="none")}
    assert runner._observations_for(envs, _tasks(*envs), run_id="r") == {}
    assert calls == []


def test_a_wider_declaration_is_probed_and_its_receipt_admits_it(monkeypatch):
    """The test that closes the defect, end to end.

    A `cpus: 4, memory_mb: 8192` declaration used to be applied as 2 cpus / 4g. Now the
    launcher receives it, the probe measures **that** envelope, and the receipt admits the
    task -- so the admission is evidence about the container the harness really runs in.
    """
    env = _env(cpus=4, memory_mb=8192)
    calls = _probe(monkeypatch)
    observations = runner._observations_for({"a": env}, _tasks("a"), run_id="r")
    assert calls[0]["spec"] == {"cpus": 4.0, "memory_mb": 8192}
    assert observations["a"]["declared"]["cpus"] == 4.0
    assert observations["a"]["declared"]["memory_mb"] == 8192
    refusal = runner._refuse_unobserved_policies(
        {"task_id": "a"}, env, None, run_id="r", observation=observations["a"])
    assert refusal is None, "the wider declaration must be admitted on its own receipt"


def test_a_declaration_the_launcher_cannot_express_gets_no_receipt(monkeypatch):
    """`limits_from` truncates `memory_mb`, so 4096.5 is applied as 4096.

    The tripwire is kept for exactly this class -- a declaration that does not survive the
    trip to the container -- and it must keep refusing rather than admit on a receipt for
    a bound the harness did not get.
    """
    calls = _probe(monkeypatch)
    assert runner._observations_for({"a": _env(memory_mb=4096.5)}, _tasks("a"),
                                    run_id="r") == {}
    assert calls == []
    # And the same shape is not retried, or 585 tasks would print it 585 times.
    assert runner._observations_for({"a": _env(memory_mb=4096.5)}, _tasks("a"),
                                    run_id="r") == {}
    assert calls == []


def test_a_probe_that_fails_is_cached_as_a_failure_and_admits_nothing(monkeypatch):
    calls = _probe(monkeypatch, raises=co.ObservationError("daemon said no"))
    observations = runner._observations_for({"a": _env(), "b": _env()},
                                            _tasks("a", "b"), run_id="r")
    assert observations == {}
    assert len(calls) == 1, "a failed probe must not be retried once per task"
    refusal = runner._refuse_unobserved_policies(
        {"task_id": "a"}, _env(), None, run_id="r", observation=None)
    assert refusal is not None
    assert refusal["invalid"]["receipt"]["reason"] == "backend_observation_unavailable"
    assert refusal["invalid"]["receipt"]["observation"] == "unknown"


def test_an_ok_false_probe_admits_nothing(monkeypatch):
    failed = _receipt() | {"ok": False, "problems": ["cpus (docker): declared 2, observed 1"]}
    _probe(monkeypatch, {AGENT: failed})
    observations = runner._observations_for({"a": _env()}, _tasks("a"), run_id="r")
    assert observations["a"]["ok"] is False
    refusal = runner._refuse_unobserved_policies(
        {"task_id": "a"}, _env(), None, run_id="r", observation=observations["a"])
    assert refusal is not None
    receipt = refusal["invalid"]["receipt"]
    assert receipt["reason"] == "backend_observation_unavailable"
    # "We looked and it did not match" is a different finding from "nobody looked".
    assert receipt["observation"] == "failed"
    assert receipt["observation_receipt"]["problems"]


def test_a_matching_receipt_admits_the_task():
    refusal = runner._refuse_unobserved_policies(
        {"task_id": "a"}, _env(), None, run_id="r", observation=_receipt())
    assert refusal is None


# --------------------------------------------------- the receipt reaches run_one ---

def test_the_receipt_reaches_run_one_for_every_task(monkeypatch):
    """`evaluate` is where the probe happens; `run_one` is where admission happens again."""
    seen: list[dict] = []

    def fake_run_one(repo, task, **kwargs):
        seen.append({"task_id": task["task_id"], "observation": kwargs.get("observation")})
        return {"task_id": task["task_id"], "answer": "", "trace": "", "exit_code": 0,
                "stderr": "", "sandbox": True,
                "verdict": {"kind": "command", "passed": True, "score": 1.0}}

    monkeypatch.setattr(runner, "run_one", fake_run_one)
    # The checkout snapshot is a 40-second stat of the whole tree and has nothing to do
    # with what this test pins.
    monkeypatch.setattr(runner, "snapshot", lambda *a, **k: {})
    monkeypatch.setattr(runner, "diff", lambda *a, **k: {})
    calls = _probe(monkeypatch)
    tasks = _tasks("a", "b", "c")
    envs = {tid: _env() for tid in ("a", "b", "c")}
    result = runner._evaluate(runner.PLATFORM_ROOT, tasks,
                              {t["task_id"]: "answer" for t in tasks}, cache={},
                              envs=envs, verifiers={}, setups={}, run_id="run-1")
    assert len(calls) == 1
    assert [entry["task_id"] for entry in seen] == ["a", "b", "c"]
    for entry in seen:
        assert entry["observation"]["ok"] is True
        assert entry["observation"]["covers"] == list(co.COVERS)
    assert len(result["env_observations"]) == 1
    assert result["env_observations"][0]["ok"] is True
    assert result["per_task"] == {"a": 1.0, "b": 1.0, "c": 1.0}


def test_without_a_receipt_the_task_is_still_refused_before_it_runs(monkeypatch):
    """The guard the wiring must not weaken: no probe, no receipt, no execution."""
    seen: list[str] = []
    monkeypatch.setattr(runner, "run_one",
                        lambda repo, task, **kwargs: seen.append(task["task_id"]))
    monkeypatch.setattr(runner, "snapshot", lambda *a, **k: {})
    monkeypatch.setattr(runner, "diff", lambda *a, **k: {})
    _probe(monkeypatch, raises=co.ObservationError("no daemon"))
    result = runner._evaluate(runner.PLATFORM_ROOT, _tasks("a"), {"a": "answer"},
                              cache={}, envs={"a": _env()}, verifiers={}, setups={},
                              run_id="run-1")
    assert seen == [], "a task with no observation must not be executed"
    assert result["per_task"] == {}
    assert result["invalid"]["a"]["detail"].startswith("backend_observation_unavailable")
    assert "env_observations" not in result


# ------------------------------------------- the probe goes to the task's network ---

def test_observe_for_probes_on_an_internal_network_and_removes_it(monkeypatch):
    """`services` is a policy word, not a docker network name: the probe makes one."""
    calls: list[list[str]] = []
    network_seen: list[str] = []

    def fake_docker(argv, stdin=None):
        calls.append(list(argv))
        if argv[:3] == ["docker", "network", "create"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:3] == ["docker", "network", "rm"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["docker", "run"]:
            network_seen.append(argv[argv.index("--network") + 1])
            assert "--read-only" in argv, "the environment container runs a read-only rootfs"
            return SimpleNamespace(returncode=0, stdout="ctr\n", stderr="")
        if argv[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps([{
                "HostConfig": {"NanoCpus": 2_000_000_000, "Memory": 4096 * 2**20,
                               "PidsLimit": 1024,
                               "NetworkMode": network_seen[-1],
                               "ReadonlyRootfs": True},
                "Config": {"User": "", "WorkingDir": "/app", "Entrypoint": ["sleep"]},
                "State": {"Pid": 4242}, "Image": "sha256:dead"}]), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(co, "_cgroup_dir", lambda pid: None)
    observation = co.observe_for(AGENT, {"cpus": 2, "memory_mb": 4096},
                                 network="services", run_id="run-9", runner=fake_docker)
    receipt = observation.receipt()
    created = [call for call in calls if call[:3] == ["docker", "network", "create"]]
    assert len(created) == 1
    assert "--internal" in created[0]
    assert created[0][created[0].index("--label") + 1] == "harnessgrad.run=run-9"
    name = created[0][created[0].index("--internal") + 1]
    assert name.startswith("hg-observe-run-9-")
    assert network_seen == [name]
    assert any(call[:3] == ["docker", "network", "rm"] for call in calls)
    assert receipt["declared"]["network"] == name
    # The cgroup files could not be read, so the probe says so rather than "ok".
    assert receipt["ok"] is False
    assert any("cpu.max" in item for item in receipt["unobserved"])


def test_observe_for_leaves_the_network_behind_only_on_no_path():
    """A refused `docker run` must not skip the network removal."""
    calls: list[list[str]] = []

    def fake_docker(argv, stdin=None):
        calls.append(list(argv))
        if argv[:3] == ["docker", "network", "create"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such image")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(co.ObservationError):
        co.observe_for(AGENT, {"cpus": 2, "memory_mb": 4096},
                       network="services", run_id="r", runner=fake_docker)
    assert [call[:3] for call in calls].count(["docker", "network", "rm"]) == 1
