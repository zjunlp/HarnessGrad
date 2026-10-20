"""The subnet allocator: deterministic, total, and able to say no.

These are the properties the platform relies on when it names a subnet instead of letting
docker pick one from its default pool (measured failure: two 16-job runs exhausted that pool
and 77 of 90 tasks in the second run were invalid). Each test below exists because some
caller depends on the answer:

* determinism -- a run receipt names the subnet a task ran on, and a crashed run that
  re-runs the same task must ask for the same subnet or the name means nothing;
* coverage -- "every candidate failed" is only an honest error if every candidate was
  actually tried;
* wrap-around -- the first candidate is a hash, so it usually starts in the middle;
* a base too small for a /24 -- refused here, because docker's own answer is 256 identical
  failures and a run that looks like a dataset bug;
* `HG_NET_SUBNET_BASE` -- the whole point is that a host whose space is already used can
  move it without a code change.

All of it is docker-free on purpose: `eval/service/netpool.py` allocates and
`services.create_network` talks to the daemon, so this file needs no daemon.
"""
from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import eval.service.netpool as netpool

ROOT = Path(__file__).resolve().parents[2]


def _pool(base: str | None = None) -> netpool.SubnetPool:
    return netpool.pool(base)


def test_the_same_task_asks_for_the_same_subnet_first():
    assert netpool.allocate("run-1", "s01").first == netpool.allocate("run-1", "s01").first
    # Different tasks start at different places: if they all started at the same /24 the
    # retry loop would serialise a whole run's task starts on one subnet.
    firsts = {netpool.allocate("run-1", f"s{i:02d}").first for i in range(20)}
    assert len(firsts) > 1


def test_the_first_subnet_is_the_same_in_a_fresh_process():
    """`hash()` is salted per process; a receipt has to survive the process that wrote it."""
    script = "import eval.service.netpool as n; print(n.allocate('run-1', 's01').first)"
    outputs = []
    for seed in ("1", "2"):
        proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                              env={**os.environ, "PYTHONHASHSEED": seed},
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout.strip())
    assert outputs[0] == outputs[1] == netpool.allocate("run-1", "s01").first


def test_the_first_subnet_is_the_hash_mapped_into_the_index_space():
    pool = _pool()
    candidates = netpool.allocate("run-7", "s03")
    assert candidates.count == 256
    assert candidates.start == pool.index_of("run-7", "s03")
    assert candidates.first == pool.at(candidates.start)
    assert list(candidates)[0] == candidates.first


@pytest.mark.parametrize("base", [None, "10.42.0.0/23", "10.42.0.0/24", "192.168.4.0/22"])
def test_every_subnet_of_the_base_is_a_candidate_exactly_once(base):
    pool = _pool(base)
    candidates = list(netpool.allocate("run-1", "s01", base=base))
    assert len(candidates) == pool.count
    assert len(set(candidates)) == pool.count
    expected = {str(net) for net in pool.base.subnets(new_prefix=netpool.SUBNET_PREFIX)}
    assert set(candidates) == expected
    assert all(ipaddress.ip_network(c).subnet_of(pool.base) for c in candidates)


def test_candidates_probe_linearly_and_wrap_around_the_space():
    pool = _pool("10.42.0.0/23")
    count = pool.count
    assert list(pool.order(count - 1)) == [pool.at(count - 1), pool.at(0)]
    # A real allocation: the order is the whole space rotated to the hash, so the last
    # candidate is the one just before it and nothing is skipped at the boundary.
    candidates = netpool.allocate("run-1", "s01", base="10.42.0.0/23")
    expected = [pool.at((candidates.start + step) % count) for step in range(count)]
    assert list(candidates) == expected
    assert list(candidates)[-1] == pool.at((candidates.start - 1) % count)


@pytest.mark.parametrize("base", ["10.42.0.0/25", "10.42.0.0/31", "10.42.0.7/32"])
def test_a_base_too_small_for_a_24_is_refused(base):
    with pytest.raises(netpool.NetPoolError) as exc:
        netpool.allocate("run-1", "s01", base=base)
    message = str(exc.value)
    assert netpool.BASE_ENV in message, "the operator must be told which variable to change"
    assert "/24" in message and base in message


@pytest.mark.parametrize("base", ["not-a-cidr", "10.42.0.5/16", "fd00::/64", "", "  "])
def test_an_unusable_base_is_refused_rather_than_guessed(base):
    with pytest.raises(netpool.NetPoolError) as exc:
        netpool.allocate("run-1", "s01", base=base)
    assert netpool.BASE_ENV in str(exc.value)


def test_the_base_can_be_a_larger_block_carved_into_24s():
    candidates = netpool.allocate("run-1", "s01", base="10.0.0.0/8")
    assert candidates.base == "10.0.0.0/8"
    assert candidates.count == 65536
    # Not materialised: a /8 is 65k candidates and the caller usually needs the first.
    assert ipaddress.ip_network(candidates.first).prefixlen == netpool.SUBNET_PREFIX


def test_the_environment_variable_moves_the_whole_space(monkeypatch):
    monkeypatch.setenv(netpool.BASE_ENV, "10.99.0.0/22")
    candidates = netpool.allocate("run-1", "s01")
    assert candidates.base == "10.99.0.0/22"
    assert candidates.count == 4
    base = ipaddress.ip_network("10.99.0.0/22")
    assert all(ipaddress.ip_network(c).subnet_of(base) for c in candidates)
    # An explicit argument beats the environment: a test or a receipt check can ask about
    # a base this process is not configured to use.
    assert netpool.allocate("run-1", "s01", base="10.77.0.0/24").base == "10.77.0.0/24"


def test_nothing_is_allocated_when_the_base_cannot_be_carved(monkeypatch):
    monkeypatch.setenv(netpool.BASE_ENV, "10.42.0.0/30")
    with pytest.raises(netpool.NetPoolError):
        netpool.pool()


# ------------------------------------------------------- reading docker's answer back ---

def _inspect(**entry) -> str:
    return json.dumps([entry])


def test_the_recorded_label_names_the_subnet_we_asked_for():
    payload = _inspect(Labels={netpool.SUBNET_LABEL: "10.42.7.0/24"},
                       IPAM={"Config": [{"Subnet": "10.42.9.0/24"}]})
    assert netpool.subnet_from_network_inspect(payload) == "10.42.7.0/24"


def test_a_network_from_before_the_label_is_read_from_ipam():
    payload = _inspect(Labels={"harnessgrad.run": "run-1"},
                       IPAM={"Config": [{"Subnet": "172.19.0.0/16"}]})
    assert netpool.subnet_from_network_inspect(payload) == "172.19.0.0/16"


@pytest.mark.parametrize("payload", [
    "", "not json", "{}", "[]", "[1,2]", '[{"Labels": null}]',
    '[{"Labels": {"harnessgrad.subnet": "nonsense"}}]',
    '[{"IPAM": {"Config": [{"Subnet": "nonsense"}]}}]',
])
def test_an_unreadable_subnet_is_none_never_a_guess(payload):
    assert netpool.subnet_from_network_inspect(payload) is None


# ------------------------------------------------------- docker's collision wording ---

@pytest.mark.parametrize("stderr", [
    # Measured, docker 29.2.1, 2026-10-19:
    "Error response from daemon: invalid pool request: Pool overlaps with other one on "
    "this address space",
    "Error response from daemon: invalid network config:\n"
    "invalid subnet 10.42.0.1/24: it should be 10.42.0.0/24",
    # Older wording, kept because the match is a substring over daemons this repo has run:
    "Error response from daemon: Address already in use",
])
def test_docker_wording_for_a_taken_subnet_is_recognised(stderr):
    assert netpool.subnet_taken(stderr) is True


@pytest.mark.parametrize("stderr", [
    "Error response from daemon: network with name hg-run-s01 already exists",
    "Error response from daemon: iptables failed",
    "",
])
def test_other_docker_failures_are_not_retried_as_collisions(stderr):
    assert netpool.subnet_taken(stderr) is False


def test_an_existing_network_is_only_recognised_by_its_own_name():
    stderr = "Error response from daemon: network with name hg-run-s01 already exists"
    assert netpool.network_exists(stderr, "hg-run-s01") is True
    assert netpool.network_exists(stderr, "hg-run-s02") is False
    # A duplicate endpoint name is not a duplicate network: `network create` cannot emit it,
    # and if it ever did, reusing the network would hide a real conflict.
    assert netpool.network_exists(
        "Error response from daemon: endpoint with name x already exists", "x") is False
    assert netpool.network_exists(
        "Error response from daemon: iptables failed", "hg-run-s01") is False
