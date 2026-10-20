"""Deterministic /24 subnets for the platform's per-task docker networks.

Why this module exists
----------------------
`eval/service/services.py:create_network` used to create each task's `--internal` network
with no `--subnet`, so the daemon carved it out of its **default address pool**. That pool
hands each user-defined network a whole block and is finite. Measured 2026-10-18 on this
host, two methods in parallel at `--jobs 16` (so up to 32 task networks alive at once):

    Error response from daemon: all predefined address pools have been fully subnetted

and 77 of the 90 tasks of the run that started second were invalid. One method at
`--jobs 16` fit; two did not. The same run was fine 15 minutes earlier, which is what made
it look like a dataset failure rather than a host limit.

The pool cannot be raised without root: `default-address-pools` lives in
`/etc/docker/daemon.json` and a change needs a daemon restart. Passing `--subnet` does not
touch the default pools at all -- docker reserves exactly the block it was handed -- so the
ceiling disappears for every user of a clone, not only for whoever can edit the daemon
config. `docs/network-subnets.md` records the measurement and the optional daemon route.

Allocating instead of relying on the daemon moves the question here: which `/24` does this
task get? Two runs must not pick the same one, a task must find the same subnet again after
a crash (a receipt names it), and an occupied subnet must not end the run. Hence:

* a `/24`, because a task network holds one harness container plus a handful of services
  (a `/24` leaves 253 addresses) and because a `/16` base then holds 256 tasks -- several
  times the largest pilot this platform runs;
* the first candidate is `sha256(run_id, task_id)` modulo the number of `/24`s, so distinct
  tasks usually start far apart and the same task always asks for the same subnet first;
* the rest of the space is then tried linearly with wrap-around, so a collision costs one
  retry rather than a task;
* `HG_NET_SUBNET_BASE` moves the whole space for a host whose `10.42.0.0/16` is already in
  use, and a base that cannot be carved into `/24`s is refused here rather than by docker.

Nothing in this module starts a process: the caller owns the `docker network create` and
this owns the arithmetic, which is what makes the arithmetic testable without docker.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass

#: The block every task subnet is carved from. Deliberately outside the `172.x` blocks the
#: daemon hands out in `/16`s and the `192.168.0.0/16` block it falls back to in `/20`s
#: (both measured, `docs/network-subnets.md`): a base that overlaps them collides with
#: networks the host already has, and every collision costs a retry.
DEFAULT_BASE = "10.42.0.0/16"

#: The override, read at allocation time. A run id is generated per run, but a dataset root
#: is not: a host whose VPN or another orchestrator owns `10.42.0.0/16` needs one variable,
#: not a code change.
BASE_ENV = "HG_NET_SUBNET_BASE"

#: The size of one task's network. `/24`, not `/16`: a task network is a harness container
#: and its services, and the space has to hold a whole pilot.
SUBNET_PREFIX = 24

#: The label that records the subnet on the network, so the answer to "which subnet did
#: task X run on?" survives the process that created it and a crashed run's network can be
#: recognised as ours.
SUBNET_LABEL = "harnessgrad.subnet"


class NetPoolError(ValueError):
    """`HG_NET_SUBNET_BASE` (or the `base` argument) is not a usable IPv4 block.

    A `ValueError` because it is bad configuration, not a transient failure: retrying it
    cannot help, and the message has to name the variable the operator has to change.
    """


@dataclass(frozen=True)
class SubnetPool:
    """A base CIDR carved into `prefix`-sized subnets.

    Pure: construction validates the block's shape, and `at`/`order` are arithmetic over
    it. Keeping docker out is what lets the allocator's properties -- coverage, wrap-around,
    a base too small to carve -- be decided by a unit test instead of by a daemon.
    """

    base: ipaddress.IPv4Network
    prefix: int = SUBNET_PREFIX

    @classmethod
    def from_cidr(cls, cidr: str) -> "SubnetPool":
        """Validate `cidr` and return the pool it defines.

        `strict=True` on purpose: `10.42.0.5/16` is a typo for a base (or a misunderstanding
        of one), and silently normalising it to `10.42.0.0/16` would allocate subnets the
        operator did not ask for and cannot predict from what they wrote.
        """
        text = str(cidr).strip()
        try:
            network = ipaddress.ip_network(text, strict=True)
        except ValueError as exc:
            raise NetPoolError(
                f"{BASE_ENV}={text!r} is not an IPv4 CIDR network: {exc}. "
                f"Pass a network address, e.g. {BASE_ENV}=10.42.0.0/16 "
                f"(the default), or unset it.") from exc
        if network.version != 4:
            raise NetPoolError(
                f"{BASE_ENV}={text!r} is an IPv6 block; task subnets are IPv4 "
                f"(the default is {DEFAULT_BASE}).")
        #: `1 << (prefix - base.prefixlen)` subnets have to fit in the base. A base smaller
        #: than one `/24` cannot be carved at all; saying so here beats 256 identical daemon
        #: errors, each of which would name a subnet the operator never asked for.
        if network.prefixlen > cls.prefix:
            raise NetPoolError(
                f"{BASE_ENV}={text!r} cannot be carved into /{cls.prefix} task networks: "
                f"it is a /{network.prefixlen} ({network.num_addresses} addresses) and one "
                f"/{cls.prefix} needs {1 << (32 - cls.prefix)}. "
                f"Use a base of /{cls.prefix} or larger, e.g. "
                f"{BASE_ENV}=10.42.0.0/16 (the default).")
        return cls(base=network, prefix=cls.prefix)

    @property
    def count(self) -> int:
        """How many task subnets this base holds."""
        return 1 << (self.prefix - self.base.prefixlen)

    def at(self, index: int) -> str:
        """The `index`-th subnet, as a CIDR string."""
        if not 0 <= index < self.count:
            raise IndexError(f"index {index} outside 0..{self.count - 1} of {self.base}")
        address = int(self.base.network_address) + index * (1 << (32 - self.prefix))
        return f"{ipaddress.IPv4Address(address)}/{self.prefix}"

    def index_of(self, *parts: object) -> int:
        """The first subnet `parts` asks for, stable across processes and machines.

        `sha256`, not `hash()`: the builtin is salted per process, so the same run would ask
        for a different subnet in a second process and no receipt could name what the first
        one used. The first eight bytes are 64 bits, which is ample for even a `/8`'s 65536
        entries.
        """
        material = "\x00".join(str(part) for part in parts)
        digest = hashlib.sha256(material.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % self.count

    def order(self, start: int) -> Iterator[str]:
        """Every subnet in the base, starting at `start` and wrapping once."""
        for step in range(self.count):
            yield self.at((start + step) % self.count)


@dataclass(frozen=True)
class Candidates:
    """The subnets to try for one task, in order, with the space they came from.

    An iterable rather than a list: with the default base there are 256 entries, but
    `HG_NET_SUBNET_BASE` may name a `/8`, and no caller wants those materialised to discover
    that the first one worked. `base`/`count` are carried so the caller's exhaustion message
    can name the block and the number of candidates without re-reading the environment.
    """

    pool: SubnetPool
    start: int

    @property
    def base(self) -> str:
        return str(self.pool.base)

    @property
    def count(self) -> int:
        return self.pool.count

    @property
    def first(self) -> str:
        """The subnet the deterministic hash asks for; what a receipt names."""
        return self.pool.at(self.start)

    def __iter__(self) -> Iterator[str]:
        return self.pool.order(self.start)


def pool(base: str | None = None) -> SubnetPool:
    """The pool this process allocates from: `base`, else `HG_NET_SUBNET_BASE`, else default.

    An empty environment variable means unset (a shell that exports nothing is not a
    configuration error), but an empty *argument* is refused: a caller that passes a base
    meant to name one.
    """
    if base is not None:
        return SubnetPool.from_cidr(base)
    return SubnetPool.from_cidr(os.environ.get(BASE_ENV) or DEFAULT_BASE)


def allocate(run_id: str, task_id: str, base: str | None = None) -> Candidates:
    """The candidate order for one task's network.

    `run_id`/`task_id`, not a single opaque key, because those are the two facts a run
    receipt already carries; anyone holding the receipt can recompute the subnet a task
    asked for first with `allocate(run_id, task_id).first`. The probe network
    (`container_observation.observe_for`) uses its own key rather than borrowing a task's,
    so a probe can never claim the subnet its own task is about to ask for.
    """
    subnets = pool(base)
    return Candidates(pool=subnets, start=subnets.index_of(run_id, task_id))


# --------------------------------------------------------------- docker's wording ---

#: What the daemon says when the subnet is not available. Measured on docker 29.2.1
#: (2026-10-19), quoted as daemon responses:
#:
#:     Error response from daemon: invalid pool request: Pool overlaps with other one on
#:     this address space
#:     Error response from daemon: invalid network config:
#:     invalid subnet 10.42.0.1/24: it should be 10.42.0.0/24
#:
#: and older daemons answered a duplicate subnet with "Address already in use". The match
#: is a case-insensitive substring over all three because this wording is not a versioned
#: interface: a marker that goes stale would turn a retryable collision into an invalid
#: task, which is the cost this whole module exists to remove.
_TAKEN_MARKERS = ("pool overlaps with other one", "address already in use", "invalid subnet")


def subnet_taken(stderr: str) -> bool:
    """True when docker refused the subnet because something else owns it (retry next)."""
    text = (stderr or "").lower()
    return any(marker in text for marker in _TAKEN_MARKERS)


def network_exists(stderr: str, name: str) -> bool:
    """True when docker refused the *name* because this network already exists.

    Matched with the name in the message and `network` in the message: `already exists`
    alone is also what a duplicate endpoint or container name reports, and that must not be
    read as "the network is already there, reuse it". Docker 29.2.1 says
    `Error response from daemon: network with name <name> already exists`.
    """
    text = (stderr or "").lower()
    return ("already exists" in text and "network" in text
            and name.lower() in text)


def subnet_from_network_inspect(payload: str) -> str | None:
    """The subnet a `docker network inspect` payload says the network has.

    The label first, because that is the value **we asked for**; the IPAM config second,
    because a network created before the label existed (every network from before this
    change) still has a subnet and can be recognised. `None` when neither is readable:
    "I could not tell" must not be spelled as any particular subnet, because the one caller
    uses this to decide whether reusing an existing network is safe.
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    entry = data[0]
    labels = entry.get("Labels")
    candidates: list[object] = []
    if isinstance(labels, dict):
        candidates.append(labels.get(SUBNET_LABEL))
    ipam = entry.get("IPAM")
    if isinstance(ipam, dict) and isinstance(ipam.get("Config"), list):
        candidates += [config.get("Subnet") for config in ipam["Config"]
                       if isinstance(config, dict)]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            return str(ipaddress.ip_network(candidate.strip(), strict=False))
        except ValueError:
            continue
    return None
