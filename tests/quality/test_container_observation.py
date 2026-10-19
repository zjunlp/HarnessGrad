"""The container observation, and the admission it can unlock.

AppWorld stopped at admission on 2026-10-18 with `backend_observation_unavailable` and
`policy_fields: env.cpus, env.exec_defaults, env.memory_mb, env.network`. The bounds *were*
applied (`eval/containers/container.py` passes `--cpus`/`--memory`/`--pids-limit` from the
task's own spec) -- what was missing was any read-back of the effective values, so the
platform had nothing to put in the receipt.

These tests fix both halves: what the probe reads, and -- the part that must never regress --
that admission still refuses when nobody looked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.run.runner import _refuse_unobserved_policies
from eval.service import container_observation as co

APPWORLD_ENV = {"kind": "exec", "state": "mount", "network": "services",
                "cpus": 2, "memory_mb": 4096}


def _fake_runner(*, nano_cpus=2_000_000_000, memory=4096 * 2**20, pids=1024,
                 network="none", readonly=True, fail_inspect=False):
    calls = []

    def run(argv, stdin=None):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0, stdout="ctr123\n", stderr="")
        if argv[:2] == ["docker", "inspect"]:
            if fail_inspect:
                return SimpleNamespace(returncode=1, stdout="", stderr="no such container")
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps([{
                "HostConfig": {"NanoCpus": nano_cpus, "Memory": memory, "PidsLimit": pids,
                               "NetworkMode": network, "ReadonlyRootfs": readonly},
                "Config": {"User": "", "WorkingDir": "/app", "Entrypoint": ["sleep"]},
                "State": {"Pid": 4242},
                "Image": "sha256:deadbeef",
            }]))
        if argv[:2] == ["docker", "rm"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run, calls


@pytest.fixture()
def cgroup(tmp_path, monkeypatch):
    def install(cpu_max="200000 100000", memory_max=str(4096 * 2**20), pids_max="1024"):
        (tmp_path / "cpu.max").write_text(cpu_max + "\n", encoding="utf-8")
        (tmp_path / "memory.max").write_text(memory_max + "\n", encoding="utf-8")
        (tmp_path / "pids.max").write_text(pids_max + "\n", encoding="utf-8")
        monkeypatch.setattr(co, "_cgroup_dir", lambda pid: tmp_path)
    return install


def test_a_matching_probe_reports_every_bound(cgroup):
    cgroup()
    run, calls = _fake_runner()
    obs = co.observe("img:test", APPWORLD_ENV, runner=run)
    assert obs.ok, obs.receipt()
    assert obs.observed["cpus_cgroup"] == 2.0
    assert obs.observed["memory_mb_cgroup"] == 4096.0
    assert any(argv[:2] == ["docker", "rm"] for argv in calls), "probe container leaked"


def test_a_lower_limit_is_reported_never_rounded_to_ok(cgroup):
    cgroup(cpu_max="150000 100000")
    run, _ = _fake_runner(nano_cpus=1_500_000_000)
    obs = co.observe("img:test", APPWORLD_ENV, runner=run)
    assert not obs.ok
    assert sum("cpus" in problem for problem in obs.problems) == 2, obs.problems


def test_an_unreadable_cgroup_is_unobserved_not_ok(cgroup):
    cgroup()
    co._cgroup_dir = lambda pid: None                          # noqa: SLF001
    run, _ = _fake_runner()
    obs = co.observe("img:test", APPWORLD_ENV, runner=run)
    assert not obs.ok and obs.unobserved and not obs.problems


def test_cleanup_runs_even_when_inspect_fails(cgroup):
    cgroup()
    run, calls = _fake_runner(fail_inspect=True)
    with pytest.raises(co.ObservationError):
        co.observe("img:test", APPWORLD_ENV, runner=run)
    assert any(argv[:2] == ["docker", "rm"] for argv in calls), "probe container leaked"


def test_admission_still_refuses_without_an_observation():
    """The safety property: not looking must never be spelled "ok"."""
    result = _refuse_unobserved_policies({"task_id": "t_1"}, APPWORLD_ENV, {})
    receipt = result["invalid"]["receipt"]
    assert receipt["reason"] == "backend_observation_unavailable"
    assert receipt["observation"] == "unknown"
    assert receipt["policy_fields"] == ["env.cpus", "env.exec_defaults", "env.memory_mb",
                                        "env.network"]


def test_a_matching_observation_admits_the_same_task(cgroup):
    cgroup()
    run, _ = _fake_runner()
    receipt = co.observe("img:test", APPWORLD_ENV, runner=run).receipt()
    assert _refuse_unobserved_policies({"task_id": "t_1"}, APPWORLD_ENV, {},
                                       observation=receipt) is None


def test_a_failed_observation_still_refuses_and_says_so(cgroup):
    cgroup(cpu_max="100000 100000")
    run, _ = _fake_runner(nano_cpus=1_000_000_000)
    receipt = co.observe("img:test", APPWORLD_ENV, runner=run).receipt()
    refusal = _refuse_unobserved_policies({"task_id": "t_1"}, APPWORLD_ENV, {},
                                          observation=receipt)
    assert refusal["invalid"]["receipt"]["observation"] == "failed"
    assert refusal["invalid"]["receipt"]["observation_receipt"]["problems"]


def test_fields_the_probe_does_not_cover_still_refuse(cgroup):
    """A CPU/memory read-back says nothing about `artifact_policy`."""
    cgroup()
    run, _ = _fake_runner()
    receipt = co.observe("img:test", APPWORLD_ENV, runner=run).receipt()
    env = {**APPWORLD_ENV, "filesystems": {"ro": ["/usr"]}}
    refusal = _refuse_unobserved_policies({"task_id": "t_1"}, env, {},
                                          observation=receipt)
    assert refusal is not None
    assert refusal["invalid"]["receipt"]["policy_fields"] == ["env.filesystems"]
    assert refusal["invalid"]["receipt"]["observation"] == "partial"
