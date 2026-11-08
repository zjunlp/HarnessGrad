"""Service setup failures must release resources already acquired by the platform."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eval.service import services


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_environment_failure_releases_the_task_network(monkeypatch, failure_type):
    acquired = []
    released = []

    def docker(*args, **kwargs):
        if args[:2] == ("network", "create"):
            acquired.append(args[3])
        elif args[:2] == ("network", "rm"):
            released.append(args[2])
        else:
            pytest.fail(f"unexpected Docker operation: {args}")
        return subprocess.CompletedProcess(args, 0, "a" * 64 if args[:2] == ("network", "create") else "", "")

    def unavailable_config():
        raise failure_type("configuration unavailable")

    monkeypatch.setattr(services.container_mod, "docker", docker)
    monkeypatch.setattr(services, "current_config", unavailable_config)
    service = {"name": "orders", "image": "sha256:" + "a" * 64,
               "needs_model": True}

    with pytest.raises(failure_type, match="configuration unavailable"):
        services.start([service], recordings_root=Path("unused-recordings"),
                       run_id="env-failure", task_id="s01", run_seed="seed")

    assert len(acquired) == 1 and acquired[0].endswith("-env-failure-s01")
    assert released == ["a" * 64]


def test_same_logical_task_gets_distinct_owned_networks_and_service_names(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    lock = threading.Lock()
    calls = []
    counter = iter(range(100))
    def docker(*args, **kwargs):
        with lock:
            calls.append(args)
            n = next(counter)
        if args[:2] == ("network", "create"):
            return subprocess.CompletedProcess(args, 0, f"{n + 1:064x}", "")
        if args[0] == "run":
            return subprocess.CompletedProcess(args, 0, f"{n + 100:064x}", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(services.container_mod, "docker", docker)
    svc = {"name": "orders", "image": "sha256:" + "a" * 64, "health": {"argv": ["true"]}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(services.start, [svc], recordings_root=tmp_path / root,
                    run_id="same", task_id="same-task", run_seed="seed")
                   for root in ("a", "b")]
        first, second = [f.result() for f in futures]
    assert first.network != second.network
    names = [args[args.index("--name") + 1] for args in calls if args[0] == "run"]
    assert len(set(names)) == 2
    network_names = [args[3] for args in calls if args[:2] == ("network", "create")]
    assert len(set(network_names)) == 2
    first.stop()
    assert ("network", "rm", first.network) in calls
    assert ("network", "rm", second.network) not in calls
    second.stop()


def test_partial_service_failure_removes_only_created_ids(monkeypatch, tmp_path):
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("network", "create"):
            return subprocess.CompletedProcess(args, 0, "a" * 64, "")
        if args[0] == "run":
            runs = [c for c in calls if c[0] == "run"]
            return subprocess.CompletedProcess(args, len(runs) == 2,
                                               "b" * 64 if len(runs) == 1 else "", "failure")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(services.container_mod, "docker", docker)
    pin = "sha256:" + "a" * 64
    with pytest.raises(services.ServiceUnavailable):
        services.start([{"name": "first", "image": pin}, {"name": "second", "image": pin}],
                       recordings_root=tmp_path, run_id="same", task_id="task", run_seed="0")
    assert ("rm", "--force", "b" * 64) in calls
    assert ("network", "rm", "a" * 64) in calls


@pytest.mark.parametrize("invalid", ["", "a" * 63, "g" * 64, "a" * 64 + "x", "short"])
def test_network_invalid_docker_id_fails_closed_without_removal(monkeypatch, invalid):
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, invalid, "")
    monkeypatch.setattr(services.container_mod, "docker", docker)
    with pytest.raises(services.ServiceUnavailable, match="usable Docker ID"):
        services.create_network("same", "task")
    assert all(args[:2] != ("network", "rm") for args in calls)


@pytest.mark.parametrize("invalid", ["", "b" * 63, "z" * 64])
def test_invalid_service_id_cleans_only_owned_network(monkeypatch, tmp_path, invalid):
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        output = "a" * 64 if args[:2] == ("network", "create") else invalid if args[0] == "run" else ""
        return subprocess.CompletedProcess(args, 0, output, "")
    monkeypatch.setattr(services.container_mod, "docker", docker)
    with pytest.raises(services.ServiceUnavailable, match="usable Docker ID"):
        services.start([{"name": "orders", "image": "sha256:" + "a" * 64}],
                       recordings_root=tmp_path, run_id="same", task_id="task", run_seed="0")
    assert ("network", "rm", "a" * 64) in calls
    assert not any(args[0] == "rm" for args in calls)
