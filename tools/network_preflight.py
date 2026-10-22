#!/usr/bin/env python3
"""Check that this host can run N concurrent task networks, before a pilot does.

The question this answers
-------------------------
"Can my host run a 90-task pilot at `--jobs 16`?" The task count is not the load: at
`--jobs 16` at most 16 task networks exist at once, because each task's `--internal`
network is created when the task starts and removed when it stops. So the host needs room
for `jobs` networks plus whatever it already has -- and before this tool there was no way
to ask. Measured 2026-10-18 on this host: two 16-job runs in parallel needed 32 task
networks, the daemon's default address pool ran out (`all predefined address pools have
been fully subnetted`), and 77 of 90 tasks in the run that started second were invalid.

What it does
------------
Creates `--networks` networks **concurrently** through the production path
(`eval.service.services.create_network`, hence the allocator in `eval/service/netpool.py`
and the real `docker network create --subnet`), prints the subnet each one got, verifies
every subnet is a `/24` inside the configured base, removes them all -- also when a create
fails -- and exits 0 only if every one was created and removed.

It is a probe, not a run: no dataset, no container, no model, and no receipt is written.
The names are `hg-netpytest-preflight-<hex>-<i>`, so a probe interleaved with a real run
cannot collide with it and an abandoned one is recognisable.

    python3 tools/network_preflight.py --networks 90 --concurrency 16

Exit codes: 0 all created and removed, 1 anything else (including leftovers), 2 `docker`
is not usable.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import eval.containers.container as container_mod                 # noqa: E402
import eval.service.netpool as netpool                             # noqa: E402
import eval.service.services as services                           # noqa: E402

#: How many networks to make by default. The platform's baseline concurrency is 16 and the
#: largest pilot it runs is 90 tasks, so 40 is more than one method's `--jobs 16` and less
#: than a 90-network sweep would ever need to coexist.
DEFAULT_NETWORKS = 40
DEFAULT_CONCURRENCY = 16


def _subnet_is_inside(subnet: str, base: ipaddress.IPv4Network) -> bool:
    try:
        return ipaddress.ip_network(subnet, strict=False).subnet_of(base)
    except (TypeError, ValueError):
        return False


def _run_id() -> str:
    """A fresh id per probe: the label filter below is what proves nothing was left behind."""
    return f"netpytest-preflight-{uuid.uuid4().hex[:8]}"


def _create_one(index: int, run_id: str) -> tuple[int, str | None, str | None, str | None]:
    """Create one network through production code. Returns (index, name, subnet, error)."""
    try:
        name = services.create_network(run_id, str(index))
    except Exception as exc:                                   # noqa: BLE001
        # The message is the operator's only actionable evidence (it names the base and
        # `HG_NET_SUBNET_BASE`), so it is reported rather than re-worded here.
        return index, None, None, f"{type(exc).__name__}: {exc}"
    return index, name, services.network_subnet(name), None


def _leftovers(run_id: str) -> list[str]:
    """Networks still carrying this probe's label. `create_network` labels every one."""
    proc = container_mod.docker("network", "ls", "--format", "{{.Name}}",
                                "--filter", f"label=harnessgrad.run={run_id}", timeout=60)
    if proc.returncode != 0:
        return []
    return proc.stdout.split()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Create N task networks concurrently, verify their subnets, remove them.")
    ap.add_argument("--networks", type=int, default=DEFAULT_NETWORKS,
                    help=f"how many networks to create at once (default {DEFAULT_NETWORKS})")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"how many creates to run in parallel (default {DEFAULT_CONCURRENCY})")
    ap.add_argument("--base", default=None,
                    help="base CIDR to carve /24s from (same as HG_NET_SUBNET_BASE); "
                         "the default is 10.42.0.0/16")
    args = ap.parse_args(argv)

    if args.networks < 1:
        ap.error("--networks must be at least 1")
    if args.concurrency < 1:
        ap.error("--concurrency must be at least 1")
    if args.base:
        os.environ[netpool.BASE_ENV] = args.base

    try:
        base = netpool.pool()
    except netpool.NetPoolError as exc:
        print(f"cannot use the configured subnet base: {exc}", file=sys.stderr)
        return 1

    try:
        container_mod.docker("version", "--format", "{{.Server.Version}}", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"docker is not usable: {exc}", file=sys.stderr)
        return 2

    run_id = _run_id()
    workers = min(args.concurrency, args.networks)
    print(f"preflight: {args.networks} networks, concurrency {workers}, "
          f"/{netpool.SUBNET_PREFIX}s from {base.base} ({base.count} available)")
    print(f"probe id {run_id} (every network is labelled harnessgrad.run={run_id})")

    created: list[str] = []
    failures: list[str] = []
    finished = 0
    lock = threading.Lock()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_create_one, index, run_id)
                       for index in range(args.networks)]
            for done in concurrent.futures.as_completed(futures):
                index, name, subnet, error = done.result()
                with lock:
                    finished += 1
                    if error is not None:
                        failures.append(f"network {index}: {error}")
                        print(f"[{finished:>3}/{args.networks}] network {index}: FAILED")
                        continue
                    created.append(name)
                    if subnet is None:
                        failures.append(f"network {index} ({name}): no subnet recorded")
                        print(f"[{finished:>3}/{args.networks}] {name}: no subnet recorded")
                    elif not _subnet_is_inside(subnet, base.base):
                        failures.append(
                            f"network {index} ({name}): subnet {subnet} is not a "
                            f"/{netpool.SUBNET_PREFIX} inside {base.base}")
                        print(f"[{finished:>3}/{args.networks}] {name}: {subnet} "
                              f"(outside {base.base})")
                    else:
                        print(f"[{finished:>3}/{args.networks}] {name}: {subnet}")
    finally:
        # Also on failure: a probe that leaves networks behind spends exactly the resource
        # it was run to protect, and `remove_network` is idempotent for a create that
        # raised after the daemon had already made the network.
        problems = [problem for name in created
                    for problem in services.remove_network(name)]
        remaining = _leftovers(run_id)
        for problem in problems:
            print(f"could not remove: {problem}", file=sys.stderr)
        if remaining:
            print(f"left {len(remaining)} network(s) behind: {' '.join(remaining)}",
                  file=sys.stderr)

    removed = len(created) - len(remaining)
    ok = (len(created) == args.networks and not failures and not remaining)
    if ok:
        print(f"{removed}/{args.networks} networks created and removed; subnets "
              f"/{netpool.SUBNET_PREFIX} from {base.base}; this host can run "
              f"{args.networks} concurrent task networks")
        return 0
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"{removed}/{args.networks} networks created and removed; subnets "
          f"/{netpool.SUBNET_PREFIX} from {base.base}; this host CANNOT run "
          f"{args.networks} concurrent task networks")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
