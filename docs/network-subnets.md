# Subnets for task networks: why two 16-job runs broke a 90-task pilot

**Status: fixed in code (2026-10-19). Every per-task `--internal` network now names its
`--subnet`; nobody needs root, a `daemon.json` edit or a daemon restart. If you only want the
check for your host, read [Check your host](#check-your-host-before-a-pilot) and run one
command.**

## The failure, measured 2026-10-18

The platform creates one docker network per task (`eval/service/services.py:create_network`),
because task *N*'s service must not be reachable from task *N+1* (INTERFACE.md §2.5.7). The
create used to pass no `--subnet`, so the daemon carved the network out of its **default
address pool**. That pool is finite.

| run | tasks | result |
| --- | --- | --- |
| one method, `--jobs 16` | 90 | **90 / 90 valid** |
| two methods in parallel (`16 + 16`) | 90 each | run that started second: **77 / 90 invalid**, daemon said `all predefined address pools have been fully subnetted` |

The daemon's words, quoted from that run:

```
Error response from daemon: all predefined address pools have been fully subnetted
```

This is the worst kind of failure the platform can produce: it happens to *tasks*, so it is
recorded as a task result, but it is a property of the **host** (how many networks already
exist), and it appears and disappears depending on what else is running. The same 90 tasks
were fine 15 minutes earlier.

## Why the default pool caps at ~15 networks

`/etc/docker/daemon.json` on this host has no `default-address-pools`, so the daemon uses its
built-in fallback, and that fallback hands **a whole block to every user-defined network**
(measured: `/16` per network, not a `/24`):

```
$ docker network inspect searxng_default \
      --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
172.18.0.0/16
```

The daemon walks a finite list of those blocks. Measured on this host, docker 29.2.1,
2026-10-19:

* `172.17.0.0/16` is the default `bridge` network;
* `172.18.0.0/16` was already a compose network (`searxng_default`) on this host;
* new networks got `172.19.0.0/16` through `172.31.0.0/16` — **13** more blocks — and then
  the walk fell through to `192.168.0.0/16` in `/20` blocks (the 14th *new* network got
  `192.168.0.0/20`).

So the first pool is about 15 blocks including `bridge` (13 free here plus the compose
network), which is where "concurrency silently caps at ~15" comes from. The exact number is
host- and version-dependent — a host whose LAN already uses `192.168.0.0/16` has no fallback,
and this daemon's second pool still holds only 16 `/20`s — but it is **always finite**, and
always smaller than a parallel pilot needs: **two 16-job runs need up to 32 task networks
alive at the same time** (this host had 29 free blocks: 13 `/16`s plus 16 `/20`s), which is
exactly the measured collision.

One task network holds a harness container, a model gateway and a handful of service
containers: a `/24` is 253 usable addresses and is already more than the task can use, while
a `/16` — or a `/20` once the first pool is exhausted — is 256 or 4096 addresses spent on a
network that cannot use them.

## The fix: every network names its subnet

`eval/service/netpool.py` allocates `/24`s from a base block and
`services.create_network` passes `--subnet <cidr>` on **every** create. When a subnet is
given, the daemon reserves exactly that block and does not touch the default pools at all, so
the ceiling disappears for every clone of this repository.

* **Base**: `10.42.0.0/16` by default → 256 task subnets. Deliberately outside the `172.x`
  and `192.168.x` blocks the daemon hands out (measured above), because a base that overlaps
  them collides with networks the host already has.
* **Override**: `HG_NET_SUBNET_BASE=<IPv4 CIDR>` moves the whole space — any base of `/24` or
  larger is accepted and carved into `/24`s. A base that cannot be carved (a `/25`, a block
  with host bits set, IPv6) is refused with a message naming the variable, before any docker
  call.
* **First candidate**: `sha256(run_id, task_id)` modulo the number of `/24`s. Deterministic
  across processes (not the salted builtin `hash()`), so the same task asks for the same
  subnet first and a run receipt can name the subnet a task ran on — the network also carries
  a `harnessgrad.subnet=<cidr>` label for exactly that.
* **Then**: the whole space linearly, wrapping once. A subnet that is already taken (another
  run, a leftover, the host's VPN) costs **one retry**, not a task. The retried errors are
  `Pool overlaps with other one`, `address already in use` and `invalid subnet`, matched
  case-insensitively because the wording is not a versioned interface.
* **Exhausted**: one `ServiceUnavailable` naming the base, the number of candidates tried and
  `HG_NET_SUBNET_BASE` — see [the error](#the-error-when-the-space-is-exhausted).
* **Already exists**: a network with this task's name is reused only when its recorded subnet
  is the one the allocator asked for (a crashed run that `reap` could not clean). A different
  subnet is a hard failure with the exact `docker network rm` to run, because the harness's
  gateway address comes from that subnet.
* **The admission probe** (`eval/service/container_observation.py:observe_for`) creates its
  own throwaway `--internal` network at admission time. It uses the same allocator with the
  key `probe-<run_id>`, so the probe cannot become the next consumer of the default pool, and
  cannot claim the subnet its own task is about to ask for.

What the explicit subnet does **not** change, measured on this host with docker 29.2.1 (the
test is `tests/quality/test_network_subnets.py::test_an_allocated_subnet_keeps_discovery_and_internal_isolation`):
a container on such a network still resolves a peer by name through the embedded DNS
(`127.0.0.11`), `NetworkMode` is still the task network, and `1.1.1.1` is still unreachable
because the network is still `--internal`.

## Check your host before a pilot

```bash
python3 tools/network_preflight.py --networks 40 --concurrency 16
```

It creates that many networks **concurrently** through the production path (the real
allocator and a real `docker network create --subnet`), prints each subnet, verifies each is a
`/24` inside the base, removes them all — also on failure — and exits `0` only if every one
was created and removed. Real output on this host, 2026-10-19:

```
preflight: 40 networks, concurrency 16, /24s from 10.42.0.0/16 (256 available)
probe id netpytest-preflight-66fb5057 (every network is labelled harnessgrad.run=netpytest-preflight-66fb5057)
[  1/40] hg-netpytest-preflight-66fb5057-1: 10.42.28.0/24
[  2/40] hg-netpytest-preflight-66fb5057-0: 10.42.113.0/24
[  3/40] hg-netpytest-preflight-66fb5057-4: 10.42.109.0/24
...
[ 40/40] hg-netpytest-preflight-66fb5057-39: 10.42.76.0/24
40/40 networks created and removed; subnets /24 from 10.42.0.0/16; this host can run 40 concurrent task networks
```

**"Can my host run a 90-task pilot at `--jobs 16`?" — yes if that command exits 0.** The
task count is not the network load: each task's network lives only while the task runs, so at
`--jobs 16` at most 16 exist at once, and 40 is more than twice the needed headroom. To prove the
stronger claim that the whole 90-task pool could be materialised at once, use
`--networks 90 --concurrency 16`; that is a bigger test than any run performs.

The probe's networks are named `hg-netpytest-preflight-<hex>-<i>` and labelled
`harnessgrad.run=<run id>`; an interrupted probe leaves recognisable networks, and the next
probe uses a fresh id. If it fails, the message is the allocator's actionable one:

```
could not create the internal network for task '31' (...): every /24 of the configured base
10.42.0.0/16 is taken (256 tried; ...). Set HG_NET_SUBNET_BASE to an unused IPv4 CIDR of /24
or larger ...
```

On a host where `10.42.0.0/16` is already in use (a VPN, another orchestrator, a previous
platform user), pick a free block and export the variable in the shell that launches the run:

```bash
export HG_NET_SUBNET_BASE=10.99.0.0/16
python3 tools/network_preflight.py --networks 40 --concurrency 16   # checks the new base
```

`--base 10.99.0.0/16` does the same for one preflight invocation, but the **run** reads the
variable, not the flag: export it in the shell that launches the driver, or pass
`HG_NET_SUBNET_BASE=10.99.0.0/16 python3 driver.py ...`, so the check and the run agree on a
base.

## The error when the space is exhausted

Quoted verbatim (docker 29.2.1, base forced to one `/24` already occupied by another network):

```
ServiceUnavailable: could not create the internal network for task 's01'
(hg-netpytest-demo-s01): every /24 of the configured base 10.88.0.0/24 is taken (1 tried;
last: 10.88.0.0/24: Error response from daemon: invalid pool request: Pool overlaps with
other one on this address space). Set HG_NET_SUBNET_BASE to an unused IPv4 CIDR of /24 or
larger (for example HG_NET_SUBNET_BASE=10.99.0.0/16) and re-run;
`python3 tools/network_preflight.py --networks 40 --concurrency 16` checks a base before a
run depends on it.
```

That is the **only** space-exhaustion answer the platform gives, and it names the one action
an operator can take. A host that hits it is not measuring anything: two 16-job runs at once
on a `10.42.0.0/16` base need 32 of its 256 subnets, so this error means something else has
taken most of the base -- a VPN, another orchestrator, or networks a killed run left behind --
or `HG_NET_SUBNET_BASE` names a block far smaller than the default.

## The optional daemon route (global, needs root and a restart)

A host that would rather fix the pool once, for every tool and not only this platform, can
make the daemon hand out `/24`s instead of `/16`s. This is **not needed** for this repository
any more: the code fix above works on an unprivileged clone, and this section is here because
an operator will ask, not because a run requires it.

```json
{
  "default-address-pools": [
    {"base": "172.80.0.0/16", "size": 24},
    {"base": "172.90.0.0/16", "size": 24}
  ]
}
```

* It needs **root** (`/etc/docker/daemon.json`) and a **daemon restart**
  (`sudo systemctl restart docker`), which interrupts every container on the host, including
  long-lived shared services. That is exactly why the platform does not depend on it.
* `size: 24` makes each network a `/24`, so these two pools hold 512 networks instead of 32.
  The bases deliberately avoid both the default bridge (`172.17.0.0/16`) and the platform's
  own `10.42.0.0/16`: a pool that overlaps either is a pool whose blocks collide with
  networks that already exist, and both sides would otherwise carve from the same addresses
  (the allocator retries, but every collision is wasted work).
* It applies only to networks created **without** `--subnet`, and it does not change networks
  that already exist. A platform clone creates every task network with `--subnet`, so the
  daemon setting changes nothing for it — the two fixes are independent, and the code fix is
  the one that ships in the repository.

## What is still finite

`10.42.0.0/16` is 256 concurrent task subnets (plus the probe's). A 90-task pilot at
`--jobs 16` needs 16; a host that wants more than 256 task networks at once sets a larger
base (`HG_NET_SUBNET_BASE=10.0.0.0/8` is 65536 `/24`s). What is *not* solved by a larger base
is a network a hard-killed run leaves behind: `services.reap(run_id)` removes labelled
containers and networks at the run's first write, `create_network` reuses a leftover whose
subnet matches, and a leftover with a different subnet fails with the command to remove it.
The preflight's own probe networks are removed in a `finally` and labelled, so an interrupted
one is identifiable rather than silent.

## Where it lives

| file | what it is |
| --- | --- |
| `eval/service/netpool.py` | the allocator: base validation, `/24` carving, `sha256` start, wrap-around, docker's collision wording, reading a network's subnet back |
| `eval/service/services.py` | `create_network` creates on an allocated subnet, retries a collision, reuses a leftover whose subnet matches, and reports exhaustion |
| `eval/service/container_observation.py` | the admission probe's throwaway network, same allocator, key `probe-<run_id>` |
| `tools/network_preflight.py` | the host check documented above |
| `tests/quality/test_netpool.py` | the allocator's properties, docker-free |
| `tests/quality/test_network_subnets.py` | `--subnet` on every create path, retry/reuse/exhaustion, 40 concurrent networks on the real daemon, and discovery + isolation on an explicit subnet |
