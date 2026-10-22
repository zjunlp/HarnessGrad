"""The task network is created on a named subnet, and never from docker's default pool.

The defect this pins, measured 2026-10-18: each `--internal` network was created with no
`--subnet`, so the daemon took it from the default address pool. That pool is finite, two
16-job runs needed 32 task networks at once, and the run that started second failed with

    Error response from daemon: all predefined address pools have been fully subnetted

leaving 77 of its 90 tasks invalid. The fix is one flag per create, so these tests are about
the flag being there on **every** path that creates a network (`services.create_network`, the
admission probe's throwaway network), about a collision retrying instead of failing a task,
and about the space running out producing one actionable error instead of 256 daemon
responses.

The two real-daemon tests are the regression test for the measurement: 40 networks created
concurrently through production code, and the same `--internal` network still resolving
service names while refusing egress. Docker-free tests fake `container_mod.docker`; they are
the ones that run anywhere.
"""
from __future__ import annotations

import ast
import concurrent.futures
import ipaddress
import json
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

import eval.containers.container as container_mod
import eval.service.container_observation as co
import eval.service.netpool as netpool
import eval.service.services as services

ROOT = Path(__file__).resolve().parents[2]
DOCKER = shutil.which("docker")

#: Measured wording, docker 29.2.1 (2026-10-19). Two of the three markers the allocator
#: recognises; the third (older daemons) is covered in `test_netpool.py`.
OVERLAP = ("Error response from daemon: invalid pool request: Pool overlaps with other "
           "one on this address space")


def _ok(args, stdout=""):
    return subprocess.CompletedProcess(args, 0, stdout, "")


def _fail(args, stderr):
    return subprocess.CompletedProcess(args, 1, "", stderr)


class Daemon:
    """`container_mod.docker` stand-in: records argv, scripts create failures, answers inspect."""

    def __init__(self, *, create: list[str] | None = None, existing: str | None = None):
        #: One entry per create attempt. "" (or an exhausted list) means success.
        self.create_errors = list(create or [])
        #: The subnet an existing network of the wanted name reports; None means not found.
        self.existing = existing
        self.calls: list[tuple[str, ...]] = []

    def docker(self, *args, timeout=None):
        self.calls.append(args)
        if args[:2] == ("network", "create"):
            error = self.create_errors.pop(0) if self.create_errors else ""
            return _fail(args, error) if error else _ok(args)
        if args[:2] == ("network", "inspect"):
            if self.existing is None:
                return _fail(args, "Error response from daemon: network not found")
            return _ok(args, json.dumps([{
                "Labels": {netpool.SUBNET_LABEL: self.existing, "harnessgrad.run": "run-1"},
                "IPAM": {"Config": [{"Subnet": self.existing}]}}]))
        return _ok(args)

    @property
    def creates(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:2] == ("network", "create")]


def _subnet_of(argv: tuple[str, ...]) -> str:
    return argv[argv.index("--subnet") + 1]


def _install(monkeypatch, daemon: Daemon) -> Daemon:
    monkeypatch.setattr(container_mod, "docker", daemon.docker)
    return daemon


# ------------------------------------------------------------ create_network ---

def test_every_create_names_a_24_inside_the_base(monkeypatch):
    daemon = _install(monkeypatch, Daemon())
    name = services.create_network("run-1", "s01")

    assert name == "hg-run-1-s01"
    assert len(daemon.creates) == 1
    argv = daemon.creates[0]
    assert "--subnet" in argv, "no --subnet means the daemon allocates from its default pool"
    subnet = _subnet_of(argv)
    assert subnet == netpool.allocate("run-1", "s01").first
    network = ipaddress.ip_network(subnet)
    assert network.prefixlen == netpool.SUBNET_PREFIX
    assert network.subnet_of(ipaddress.ip_network(netpool.DEFAULT_BASE))
    assert "--internal" in argv
    assert argv[argv.index("--internal") + 1] == "hg-run-1-s01"
    assert "harnessgrad.run=run-1" in argv
    assert "harnessgrad.task=s01" in argv
    assert f"{netpool.SUBNET_LABEL}={subnet}" in argv
    assert daemon.calls[0][:2] == ("network", "create")


def test_a_taken_subnet_retries_the_next_candidate(monkeypatch):
    daemon = _install(monkeypatch, Daemon(create=[OVERLAP]))
    assert services.create_network("run-1", "s01") == "hg-run-1-s01"

    candidates = list(netpool.allocate("run-1", "s01"))
    assert len(daemon.creates) == 2, "a collision must cost one retry, not the task"
    assert [_subnet_of(call) for call in daemon.creates] == candidates[:2]
    assert all(call[call.index("--internal") + 1] == "hg-run-1-s01"
               for call in daemon.creates)


def test_every_candidate_is_tried_before_the_space_is_called_exhausted(monkeypatch):
    daemon = _install(monkeypatch, Daemon(create=[OVERLAP] * 1000))
    with pytest.raises(services.ServiceUnavailable) as exc:
        services.create_network("run-1", "s01")

    subnets = [_subnet_of(call) for call in daemon.creates]
    assert len(subnets) == 256, "the error may only say 'all taken' if all were tried"
    assert len(set(subnets)) == 256
    message = str(exc.value)
    assert netpool.DEFAULT_BASE in message, "the operator has to know which base ran out"
    assert netpool.BASE_ENV in message, "and which variable moves it"
    assert "s01" in message
    assert "network_preflight" in message


def test_a_small_base_reports_itself_and_its_one_candidate(monkeypatch):
    monkeypatch.setenv(netpool.BASE_ENV, "10.77.0.0/24")
    daemon = _install(monkeypatch, Daemon(create=[OVERLAP]))
    with pytest.raises(services.ServiceUnavailable) as exc:
        services.create_network("run-1", "s01")
    assert len(daemon.creates) == 1
    message = str(exc.value)
    assert "10.77.0.0/24" in message and netpool.BASE_ENV in message


def test_an_existing_network_with_the_same_subnet_is_reused(monkeypatch):
    """A crashed run reaped badly leaves the name and the subnet the task will ask for."""
    subnet = netpool.allocate("run-1", "s01").first
    daemon = _install(monkeypatch, Daemon(
        create=["Error response from daemon: network with name hg-run-1-s01 already exists"],
        existing=subnet))
    assert services.create_network("run-1", "s01") == "hg-run-1-s01"
    assert len(daemon.creates) == 1, "reuse is decided on the first candidate, not by retrying"
    assert any(call[:2] == ("network", "inspect") for call in daemon.calls)


def test_an_existing_network_with_another_subnet_is_a_failure(monkeypatch):
    """The harness's gateway address comes from the subnet; reusing a different one lies."""
    subnet = netpool.allocate("run-1", "s01").first
    daemon = _install(monkeypatch, Daemon(
        create=["Error response from daemon: network with name hg-run-1-s01 already exists"],
        existing="172.19.0.0/16"))
    with pytest.raises(services.ServiceUnavailable) as exc:
        services.create_network("run-1", "s01")
    assert len(daemon.creates) == 1
    message = str(exc.value)
    assert "172.19.0.0/16" in message and subnet in message
    assert "docker network rm hg-run-1-s01" in message


def test_an_existing_network_whose_subnet_cannot_be_read_is_a_failure(monkeypatch):
    daemon = _install(monkeypatch, Daemon(
        create=["Error response from daemon: network with name hg-run-1-s01 already exists"],
        existing=None))
    with pytest.raises(services.ServiceUnavailable) as exc:
        services.create_network("run-1", "s01")
    assert len(daemon.creates) == 1
    assert "unknown" in str(exc.value), "'could not read' must not be spelled as a match"


def test_a_failure_that_is_not_a_collision_is_not_retried(monkeypatch):
    daemon = _install(monkeypatch, Daemon(
        create=["Error response from daemon: iptables failed: ..."]))
    with pytest.raises(services.ServiceUnavailable) as exc:
        services.create_network("run-1", "s01")
    assert len(daemon.creates) == 1
    assert "iptables failed" in str(exc.value)


def test_an_unusable_base_is_reported_before_any_docker_call(monkeypatch):
    monkeypatch.setenv(netpool.BASE_ENV, "10.42.0.0/25")
    daemon = _install(monkeypatch, Daemon())
    with pytest.raises(services.ServiceUnavailable) as exc:
        services.create_network("run-1", "s01")
    assert netpool.BASE_ENV in str(exc.value)
    assert "10.42.0.0/25" in str(exc.value)
    assert daemon.calls == []


# ---------------------------------------------------- the admission probe's network ---

def _probe_runner(calls: list[list[str]], create_errors: list[str] | None = None):
    """A docker runner for `observe_for`: scripts the probe's network creates only."""
    pending = list(create_errors or [])

    def runner(argv, stdin=None):
        calls.append(list(argv))
        if argv[:3] == ["docker", "network", "create"]:
            error = pending.pop(0) if pending else ""
            return _fail(argv, error) if error else _ok(argv)
        if argv[:3] == ["docker", "network", "rm"]:
            return _ok(argv)
        if argv[:2] == ["docker", "run"]:
            return _ok(argv, "ctr\n")
        if argv[:2] == ["docker", "inspect"]:
            network = next((call[call.index("--network") + 1] for call in calls
                            if call[:2] == ["docker", "run"] and "--network" in call), "none")
            return _ok(argv, json.dumps([{
                "HostConfig": {"NanoCpus": 2_000_000_000, "Memory": 4096 * 2**20,
                               "PidsLimit": 1024, "NetworkMode": network,
                               "ReadonlyRootfs": True},
                "Config": {"User": "", "WorkingDir": "/app", "Entrypoint": ["sleep"]},
                "State": {"Pid": 4242}, "Image": "sha256:dead"}]))
        return _ok(argv)

    return runner


def test_the_probe_network_also_gets_an_allocated_subnet(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(co, "_cgroup_dir", lambda pid: None)
    co.observe_for("image@sha256:" + "a" * 64, {"cpus": 2, "memory_mb": 4096},
                   network="services", run_id="run-9", runner=_probe_runner(calls))

    created = [call for call in calls if call[:3] == ["docker", "network", "create"]]
    assert len(created) == 1
    argv = created[0]
    assert "--subnet" in argv, "the probe must not spend a block from the default pool"
    subnet = _subnet_of(argv)
    assert ipaddress.ip_network(subnet).prefixlen == netpool.SUBNET_PREFIX
    name = argv[argv.index("--internal") + 1]
    assert name.startswith("hg-observe-run-9-")
    # The same allocator, distinct key: a probe never asks for the subnet a task will want.
    assert subnet == netpool.allocate("probe-run-9", name).first
    assert f"{netpool.SUBNET_LABEL}={subnet}" in argv
    assert "harnessgrad.run=run-9" in argv
    # And it is removed on the way out, as before.
    assert any(call[:3] == ["docker", "network", "rm"] for call in calls)


def test_the_probe_retries_a_taken_subnet(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(co, "_cgroup_dir", lambda pid: None)
    co.observe_for("image@sha256:" + "a" * 64, {"cpus": 2, "memory_mb": 4096},
                   network="services", run_id="run-9",
                   runner=_probe_runner(calls, [OVERLAP]))
    created = [call for call in calls if call[:3] == ["docker", "network", "create"]]
    assert len(created) == 2
    name = created[0][created[0].index("--internal") + 1]
    expected = list(netpool.allocate("probe-run-9", name))
    assert [_subnet_of(call) for call in created] == expected[:2]


def test_the_probe_reports_an_exhausted_base_as_an_observation_error(monkeypatch):
    monkeypatch.setenv(netpool.BASE_ENV, "10.77.0.0/24")
    calls: list[list[str]] = []
    with pytest.raises(co.ObservationError) as exc:
        co.observe_for("image@sha256:" + "a" * 64, {"cpus": 2, "memory_mb": 4096},
                       network="services", run_id="run-9",
                       runner=_probe_runner(calls, [OVERLAP]))
    message = str(exc.value)
    assert "10.77.0.0/24" in message and netpool.BASE_ENV in message
    created = [call for call in calls if call[:3] == ["docker", "network", "create"]]
    assert len(created) == 1


def test_the_probe_reports_an_unusable_base_as_an_observation_error(monkeypatch):
    monkeypatch.setenv(netpool.BASE_ENV, "10.42.0.0/25")
    with pytest.raises(co.ObservationError) as exc:
        co.observe_for("image@sha256:" + "a" * 64, {"cpus": 2, "memory_mb": 4096},
                       network="services", run_id="run-9", runner=_probe_runner([]))
    assert netpool.BASE_ENV in str(exc.value)


# ------------------------------------------------- no default-pool create is left ---

def _network_create_sites(path: Path) -> list[tuple[str, int, list[str]]]:
    """Every literal `network create` argv in a production module.

    Two shapes: `container_mod.docker("network", "create", ...)` and the probe's
    `runner(["docker", "network", "create", ...])`. A new third shape is a site this
    test does not see, which is why the real-daemon test exists as well.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sites: list[tuple[str, int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        argv: list[str] | None = None
        positional = node.args
        if (len(positional) >= 2 and all(isinstance(arg, ast.Constant)
                                         for arg in positional[:2])
                and [positional[0].value, positional[1].value] == ["network", "create"]):
            argv = [arg.value for arg in positional if isinstance(arg, ast.Constant)]
        elif positional and isinstance(positional[0], ast.List):
            constants = [item.value for item in positional[0].elts
                         if isinstance(item, ast.Constant)]
            if constants[:3] == ["docker", "network", "create"]:
                argv = constants
        if argv is not None:
            sites.append((str(path.relative_to(ROOT)), node.lineno, argv))
    return sites


def test_no_production_path_creates_a_network_without_a_subnet():
    files = sorted((ROOT / "eval").rglob("*.py"))
    sites = [site for path in files for site in _network_create_sites(path)]
    assert len(sites) >= 2, f"expected the task and probe creates, found {sites}"
    missing = [f"{path}:{line}" for path, line, argv in sites if "--subnet" not in argv]
    assert not missing, (
        "these creates allocate from docker's default address pool, which is what caps a "
        f"host at ~15 task networks: {missing}")


# -------------------------------------------------------------- real docker ---

def _require_docker() -> None:
    if DOCKER is None:
        pytest.skip("docker is not installed")
    proc = container_mod.docker("version", "--format", "{{.Server.Version}}", timeout=60)
    if proc.returncode != 0:
        pytest.skip(f"docker daemon is not usable: {proc.stderr.strip()[:200]}")


def _labelled(run_id: str) -> list[str]:
    proc = container_mod.docker("network", "ls", "--format", "{{.Name}}",
                                "--filter", f"label=harnessgrad.run={run_id}", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.split()


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_forty_task_networks_exist_at_once_and_are_all_removed():
    """The measured failure was two 16-job runs; 40 networks at once is the regression test.

    Concurrent creates go through `services.create_network`, so the allocator's hash
    collisions and docker's concurrent `--subnet` checks are both exercised. Before the
    fix, the 15th-or-so create failed with `all predefined address pools have been fully
    subnetted`; with `--subnet` the same daemon accepts all 40.
    """
    _require_docker()
    run_id = f"netpytest-{uuid.uuid4().hex[:10]}"
    created: list[str] = []
    lock = threading.Lock()

    def one(index: int):
        name = services.create_network(run_id, f"t{index:02d}")
        with lock:
            created.append(name)
        return name, services.network_subnet(name)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(one, range(40)))
        assert len(results) == 40
        assert len({name for name, _ in results}) == 40
        subnets = [subnet for _, subnet in results]
        assert all(subnet is not None for subnet in subnets), results
        assert len(set(subnets)) == 40, "two tasks cannot share an allocated subnet"
        base = ipaddress.ip_network(netpool.pool().base)
        for subnet in subnets:
            network = ipaddress.ip_network(subnet)
            assert network.prefixlen == netpool.SUBNET_PREFIX
            assert network.subnet_of(base)
        assert sorted(_labelled(run_id)) == sorted(created)
    finally:
        for name in created:
            services.remove_network(name)
        for name in _labelled(run_id):           # a create that raised after the daemon made it
            services.remove_network(name)
    assert _labelled(run_id) == [], "the regression test must not spend the pool it tests"


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_an_allocated_subnet_keeps_discovery_and_internal_isolation():
    """`--subnet` must not change what the network *is*: names resolve, egress stays shut.

    If it did, the fix would trade one failure for a quieter one: a harness that cannot find
    its service, or one that now has the internet. Measured on this host, docker 29.2.1:
    the peer resolves through 127.0.0.11 and `1.1.1.1` is unreachable.
    """
    _require_docker()
    image = "alpine:latest"
    if container_mod.docker("image", "inspect", image, timeout=60).returncode != 0:
        pytest.skip(f"{image} is not present locally (not pulling in a test)")

    run_id = f"netpytest-dns-{uuid.uuid4().hex[:8]}"
    peer = f"hg-netpytest-peer-{uuid.uuid4().hex[:6]}"
    network = services.create_network(run_id, "probe")
    try:
        subnet = services.network_subnet(network)
        assert subnet is not None
        assert ipaddress.ip_network(subnet).prefixlen == netpool.SUBNET_PREFIX
        inspected = json.loads(container_mod.docker("network", "inspect", network).stdout)
        assert inspected[0]["Internal"] is True, "an explicit subnet must not drop --internal"

        started = container_mod.docker("run", "-d", "--rm", "--name", peer,
                                       "--network", network, "--entrypoint", "sleep",
                                       image, "120")
        assert started.returncode == 0, started.stderr
        resolved = container_mod.docker("run", "--rm", "--network", network,
                                        "--entrypoint", "nslookup", image, peer)
        assert resolved.returncode == 0, resolved.stderr
        assert peer in resolved.stdout, resolved.stdout
        egress = container_mod.docker(
            "run", "--rm", "--network", network, "--entrypoint", "sh", image, "-c",
            "wget -T 3 -q -O- http://1.1.1.1 >/dev/null 2>&1; echo $?")
        assert egress.returncode == 0, egress.stderr
        assert egress.stdout.strip() != "0", "the internal network must not reach the internet"
    finally:
        container_mod.docker("rm", "--force", peer, timeout=60)
        services.remove_network(network)
