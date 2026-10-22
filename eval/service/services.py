"""Services: containers that run alongside a task. INTERFACE.md §2.5.7.

A service is a mock API, a database, a browser, a simulated user — something that has
to be running *while* the harness runs and reachable only from inside the task's
environment.

This module owns the lifecycle and nothing else: `eval/containers/container.py` is the docker
primitive layer, and what is here is the order and the rules, which are the parts that
are hard to get right.

Three of those rules are the whole design:

**Per-task network, `--internal`.** Per task rather than per run, because task *N*'s
service must not be reachable from task *N+1*: a shared network would make every
service in a run reachable from every task in it, which is a cross-task leak of exactly
the kind this platform otherwise spends its effort preventing. `--internal` because it
is what makes "reachable, and not on the internet" true rather than hoped for —
measured on this host: a peer on such a network resolves and answers, `1.1.1.1` does
not, and DNS fails.

**The recording is mounted into the service and the check, and never into the
harness.** Every input before this one was static, so re-materializing it defeated
editing (§2.2). A recording is produced while the harness runs, so there is nothing to
re-materialize and the only available rule is a mount rule. `recordings_for` is
therefore the single place that decides who sees it, and the harness is not in the
list.

**A service that is not healthy refuses the task.** Not a zero: a zero is
indistinguishable from a weak harness, and the difference is the entire reason this
platform exists. `ServiceUnavailable` carries enough to attribute the failure to the
dataset and the service by name.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import eval.containers.container as container_mod
import eval.service.netpool as netpool
from harnessgrad.config import current as current_config

#: Where a service's recording appears, inside the service container and inside the
#: check. **Fixed by the platform and never named by the dataset** — a dataset-chosen
#: path is a path chosen by something a method may influence (§2.5.8).
RECORDINGS_ROOT = "/recordings"

#: The one filename inside a recording that the platform itself reads. Everything else
#: in the directory belongs to the dataset's check.
USAGE_FILE = "harnessgrad.json"

#: How often to re-run a health check while waiting for it to pass.
HEALTH_POLL_S = 0.5


class ServiceUnavailable(RuntimeError):
    """A service could not be started, or never became healthy.

    Distinct from a harness failure on purpose: this is a **dataset-side** failure, and
    INTERFACE.md §2.5.7 requires it to be attributed rather than scored.
    """

    def __init__(self, message: str, *, service: str | None = None,
                 stage: str = "start"):
        super().__init__(message)
        self.service = service
        #: `start`, `health` or `died` -- which part of the lifecycle failed. Recorded
        #: because "the mock API never answered its own health check" and "the mock API
        #: crashed halfway through the task" are different dataset bugs.
        self.stage = stage


# ------------------------------------------------------------------ names/env ---

def label(name: str) -> str:
    """A docker-legal name fragment, and the harness-facing variable name.

    Not sanitized silently in the dataset layer (`registry.SERVICE_NAME_RE` already
    restricts it to a DNS label), so this only has to handle the run id, which the
    platform generates and which may contain characters docker rejects.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name)


#: Kept so the module's own call sites read unchanged.
_label = label


def url_var(service_name: str) -> str:
    """The variable the harness reads to find a service.

    `HG_SVC_*`, not `HG_ENV_*`: the two prefixes have two different audiences, and a
    single prefix would make "may the harness see this" a question a reader answers by
    knowing which keys happen to exist — which is how a credential leaks (§2.5.7).
    """
    return f"HG_SVC_{service_name.upper().replace('-', '_')}_URL"


def harness_env(services: list[dict]) -> dict[str, str]:
    """What the harness is told about the services, and all it is told.

    The URL rather than the address: an address is a fact about one docker network,
    and the name is the thing the dataset declared and can reason about.
    """
    return {url_var(svc["name"]): f"http://{svc['name']}:{svc['port']}"
            for svc in services}


def service_env(services: list[dict], *, task_id: str, run_seed: str) -> dict[str, str]:
    """What the *services* get, and the harness never does.

    A third model budget, after the harness's and the method's, separated the same way
    the other two are: `harness_env()` strips `HG_METHOD_*`, and this is handed only to
    the service containers. An environment whose simulator can spend the agent's
    credential is an environment that can impersonate the agent.
    """
    # A service is scoped to one task.  Exposing this identifier lets an image bind its
    # API to the current task without trusting the request body to choose a dataset row.
    env: dict[str, str] = {"HG_ENV_TASK_ID": task_id}
    if any(svc.get("needs_seed") for svc in services):
        env["HG_ENV_SEED"] = task_seed(run_seed, task_id)
    if any(svc.get("needs_model") for svc in services):
        # The third model budget, from the declared configuration rather than from
        # `os.environ` at the point of use. The variable names stay the same because a
        # service image reads them by name; only the platform's read moved.
        environment = current_config().environment
        for key, value in (("HG_ENV_MODEL", environment.model),
                           ("HG_ENV_API_KEY", environment.api_key),
                           ("HG_ENV_BASE_URL", environment.base_url)):
            if value:
                env[key] = value
    return env


def task_seed(run_seed: str, task_id: str) -> str:
    """A per-task seed derived from the run's, so it is reproducible from the record.

    Derived rather than drawn: a seed chosen at run time and written down is
    reproducible only if the record survives, and a seed derived from `(run_seed,
    task_id)` is reproducible from two facts the curve already carries (§2.5.4).
    """
    import hashlib
    return hashlib.sha256(f"{run_seed}:{task_id}".encode()).hexdigest()[:16]


def requires_model(services: list[dict]) -> bool:
    return any(svc.get("needs_model") for svc in services)


def requires_seed(services: list[dict]) -> bool:
    return any(svc.get("needs_seed") for svc in services)


# ------------------------------------------------------------------ lifecycle ---

class RunningServices:
    """The containers started for one task, and the network they are on.

    Held as one object so that teardown cannot be forgotten piecemeal: `stop` is the
    only way to end a task's services and it removes the network too. A leaked network
    is the failure mode here — containers carry `--rm`, networks do not.
    """

    def __init__(self, *, network: str, containers: dict[str, str],
                 recordings: dict[str, Path], services: list[dict]):
        self.network = network
        self.containers = containers
        self.recordings = recordings
        self.services = services

    def urls(self) -> dict[str, str]:
        return {svc["name"]: f"http://{svc['name']}:{svc['port']}"
                for svc in self.services}

    def usage(self) -> dict:
        """The environment's own model spend, read from the recordings.

        Summed from `harnessgrad.json` in each recording rather than asked for over the
        network: the service has stopped or is about to, and a number the platform asks
        for is a number it can be lied to about. `eval/run/runner.py` reads a harness's
        usage from its trace for the same reason.
        """
        total: dict[str, int] = {}
        reported = 0
        for name, path in self.recordings.items():
            record = path / USAGE_FILE
            if not record.is_file():
                continue
            try:
                payload = json.loads(record.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                continue
            reported += 1
            for key in ("input_tokens", "output_tokens", "calls"):
                value = usage.get(key)
                if isinstance(value, int):
                    total[key] = total.get(key, 0) + value
        total["services_reported"] = reported
        return total

    def stop(self) -> list[str]:
        """Tear down containers and the network. Never raises.

        Never raises because it runs in a `finally`, and a teardown that can throw
        replaces the real failure with a misleading one. It returns what it could not
        remove so a caller that cares (a test) can say so.
        """
        problems: list[str] = []
        for name, cid in self.containers.items():
            # --rm destroys the service's only startup/action diagnostics. Keep them
            # beside its recording before removal, including on failed health checks.
            record = self.recordings.get(name)
            if record is not None:
                try:
                    logs = container_mod.docker("logs", "--tail", "200", cid, timeout=10)
                    (record / "service.log").write_text(
                        logs.stdout + logs.stderr, encoding="utf-8")
                except (subprocess.TimeoutExpired, OSError):
                    pass  # Evidence collection must never prevent teardown.
            try:
                proc = container_mod.docker("rm", "--force", cid, timeout=60)
            except (subprocess.TimeoutExpired, OSError) as exc:
                problems.append(f"service {name!r}: {exc}")
                continue
            if proc.returncode != 0 and "No such container" not in proc.stderr:
                problems.append(f"service {name!r}: {proc.stderr.strip()[:200]}")
        problems.extend(remove_network(self.network))
        return problems


def create_network(run_id: str, task_id: str) -> str:
    """Create the task's `--internal` network. Returns its name.

    Public because a task needs one even when it declares no services: a containerised
    harness cannot reach a model on the host's loopback, so the platform publishes the
    endpoint on this network (`eval/service/modelgate.py`). Without that the harness has no
    model, and a harness with no model is not being measured.

    **Every create carries an explicit `--subnet`** from `eval/service/netpool.py`. Without
    one the daemon carves the network out of its default address pool, which hands out a
    whole `/16` per network and is finite: measured 2026-10-18, two methods in parallel at
    `--jobs 16` exhausted it (`all predefined address pools have been fully subnetted`) and
    77 of 90 tasks in the run that started second were invalid. A subnet the caller names
    bypasses the default pools entirely, so the ceiling is gone without root, a
    `daemon.json` edit or a daemon restart (`docs/network-subnets.md`).

    A collision on the subnet is not a failure: the allocator's next candidate is tried.
    "It is already taken" is the expected case on a host that has been running for a while,
    and turning it into an invalid task is exactly the defect the explicit subnet fixes.
    """
    net = label(f"hg-{run_id}-{task_id}")
    try:
        candidates = netpool.allocate(run_id, task_id)
    except netpool.NetPoolError as exc:
        # Bad configuration, not a transient failure: the allocator's message names the
        # variable to change. Translated so every caller of `create_network` keeps its
        # existing "this task is invalid" path instead of crashing the run on a ValueError.
        raise ServiceUnavailable(
            f"could not create the internal network for task {task_id!r}: {exc}") from exc
    taken: list[str] = []
    for cidr in candidates:
        proc = container_mod.docker("network", "create", "--internal", net,
                                    "--subnet", cidr,
                                    "--label", f"harnessgrad.run={run_id}",
                                    "--label", f"harnessgrad.task={task_id}",
                                    "--label", f"{netpool.SUBNET_LABEL}={cidr}",
                                    timeout=120)
        if proc.returncode == 0:
            return net
        stderr = proc.stderr.strip()
        if netpool.network_exists(stderr, net):
            existing = network_subnet(net)
            if existing == cidr:
                # A crashed run that was reaped badly leaves the name *and* the subnet we
                # are about to ask for, because both are a function of (run_id, task_id).
                # Reusing it is what keeps that crash out of the next run's measurement;
                # `reap` remains the cleaner path, it just may have lost a container.
                return net
            raise ServiceUnavailable(
                f"network {net!r} already exists with subnet "
                f"{existing or 'unknown'}, but this task asked for {cidr}. Refusing to "
                f"reuse it: the harness's gateway address comes from that subnet. Remove "
                f"the leftover with `docker network rm {net}` (or run "
                f"`eval.service.services.reap({run_id!r})`) and re-run.")
        if netpool.subnet_taken(stderr):
            taken.append(f"{cidr}: {stderr.splitlines()[-1][:160]}")
            continue
        raise ServiceUnavailable(
            f"could not create the internal network for task {task_id!r}: "
            f"{stderr[:300]}")
    raise ServiceUnavailable(
        f"could not create the internal network for task {task_id!r} ({net}): every "
        f"/{netpool.SUBNET_PREFIX} of the configured base {candidates.base} is taken "
        f"({candidates.count} tried; last: {taken[-1] if taken else 'none'}). "
        f"Set {netpool.BASE_ENV} to an unused IPv4 CIDR of /{netpool.SUBNET_PREFIX} or "
        f"larger (for example {netpool.BASE_ENV}=10.99.0.0/16) and re-run; "
        f"`python3 tools/network_preflight.py --networks 40 --concurrency 16` checks a "
        f"base before a run depends on it.")


def network_subnet(network: str) -> str | None:
    """The subnet a network was created with, or `None` when it cannot be read.

    Public because `tools/network_preflight.py` reports what the allocator actually got,
    and because `create_network` needs the same answer to decide whether an existing
    network of its name is the one it would have created. The parse is
    `netpool.subnet_from_network_inspect` so a probe network is read the same way.
    """
    try:
        proc = container_mod.docker("network", "inspect", network, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return netpool.subnet_from_network_inspect(proc.stdout)


def remove_network(network: str) -> list[str]:
    """Remove a network. Never raises, like `RunningServices.stop`."""
    try:
        proc = container_mod.docker("network", "rm", network, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"network {network!r}: {exc}"]
    # Docker 29 uses "network NAME not found"; older daemons use "No such
    # network". Reaping followed by a finally-block stop must remain idempotent.
    missing = ("No such network" in proc.stderr or
               f"Error response from daemon: network {network} not found"
               in proc.stderr.splitlines())
    if proc.returncode != 0 and not missing:
        return [f"network {network!r}: {proc.stderr.strip()[:200]}"]
    return []


def reap(run_id: str) -> list[str]:
    """Remove containers and networks a **previous crash** left under this run id.

    Measured (2026-10-04): a run killed mid-task leaves its per-task networks and any
    running service container behind -- `local-floor-5/6` and `local-cap12` each left
    one network per task, and a probe run left a service container up for 15 hours.
    `docker run --rm` does not help: it removes a container when it *exits*, and a
    killed driver never stops it. Ctrl-C is handled (`except BaseException` around the
    service start path), but SIGTERM and SIGKILL are not, and neither is a test that
    is interrupted.

    The cost is not disk. It is that the **next** run with the same id dies at
    `docker network create ... already exists`, so a measurement is lost to a failure
    that belongs to the previous crash and is reported as if it were about this run.

    Called at the run's first write, beside the same decision for the run directory
    (`_write_run_meta(..., fresh=True)`): a reused id means "start clean". Only this
    run id's objects are touched, so a different run executing concurrently is not
    disturbed. Never raises; returns what it could not remove.
    """
    problems: list[str] = []
    selector = f"label=harnessgrad.run={run_id}"
    #: And by **name**, because the label is not always there. The harness container was
    #: created with only `--name` until 2026-10-04 (`eval/containers/container.py:create` now labels
    #: it too), so the containers a hard-killed run actually leaves behind are exactly the
    #: ones a label search cannot see -- measured on `screen-all-1`: five containers still
    #: `running`, and this function reported only "network ... has active endpoints".
    #: `hg-<run_id>-` is the platform's own naming (`_task_name`), so matching it cannot
    #: reach another run's objects.
    prefix = f"hg-{run_id}-"
    for kind, list_args, remove_args, by_name in (
            ("container", ["container", "ls", "--all", "--quiet"], ["rm", "--force"],
             True),
            ("network", ["network", "ls", "--quiet"], ["network", "rm"], True)):
        found: list[str] = []
        for flt in ([selector] + ([f"name={prefix}"] if by_name else [])):
            try:
                listed = container_mod.docker(*list_args, "--filter", flt, timeout=60)
            except (subprocess.TimeoutExpired, OSError) as exc:
                problems.append(f"{kind} list ({flt}): {exc}")
                continue
            if listed.returncode != 0:
                problems.append(f"{kind} list ({flt}): {listed.stderr.strip()[:200]}")
                continue
            found += [i for i in listed.stdout.split() if i not in found]
        for ident in found:
            try:
                removed = container_mod.docker(*remove_args, ident, timeout=120)
            except (subprocess.TimeoutExpired, OSError) as exc:
                problems.append(f"{kind} {ident[:12]}: {exc}")
                continue
            if removed.returncode != 0:
                problems.append(f"{kind} {ident[:12]}: {removed.stderr.strip()[:200]}")
    return problems


def gateway_ip(network: str) -> str:
    """The host-side address of a task network, which is where a gateway can bind.

    The host is the gateway of a docker network, so a process bound here is reachable
    from inside the network and unreachable from outside it -- measured, and it is the
    property the model gateway depends on.
    """
    proc = container_mod.docker("network", "inspect", network, "-f",
                                "{{(index .IPAM.Config 0).Gateway}}", timeout=60)
    ip = proc.stdout.strip()
    if proc.returncode != 0 or not ip:
        raise ServiceUnavailable(
            f"could not read the gateway address of network {network!r}: "
            f"{proc.stderr.strip()[:200]}")
    return ip


def recording_host(recordings_root: Path, task_id: str, name: str) -> Path:
    """The **absolute** host directory a recording is written to and mounted from.

    Docker refuses a relative bind source (`invalid mount path: 'runs/...'`), and the
    documented default `--runs-root` is the CWD-relative `runs` (`driver.py`), so a run
    started without an absolute root handed `runs/<run-id>/recordings/<task>/<service>`
    straight to `--mount` and every service task died at start. Resolution belongs
    here, at the mount, rather than at the caller: the recording still lands at the
    same on-disk location, and a caller that names a relative root gets the same
    directory made absolute. This is the one mount source the platform derives from a
    user-supplied path; `task_dir` and the work tree are already absolute.
    """
    return (Path(recordings_root) / task_id / name).resolve()


def start(services: list[dict], *, recordings_root: Path, run_id: str,
          task_id: str, run_seed: str) -> RunningServices:
    """Create the network, start every service, and wait for each to be healthy.

    Raises `ServiceUnavailable` — having already torn down whatever it started, so a
    failed task does not leave a network behind for the next one to inherit.
    """
    # The same safety net `eval/container.run` carries, for the same reason: the driver
    # pins every service image before the run starts (§2.5.4), and a service started
    # from a tag would pull whatever the registry served that day.
    for svc in services:
        if not container_mod.looks_pinned(svc["image"]):
            raise ServiceUnavailable(
                f"service {svc['name']!r} names {svc['image']!r}, which is not pinned "
                f"to a content address. Resolve it with `container.resolve_digest` "
                f"first (INTERFACE.md §2.5.4).", service=svc["name"], stage="start")

    net = create_network(run_id, task_id)

    containers: dict[str, str] = {}
    recordings: dict[str, Path] = {}
    try:
        # Configuration can fail after network creation; keep it inside the same
        # cleanup boundary as container startup.
        env = service_env(services, task_id=task_id, run_seed=run_seed)
        for svc in services:
            name = svc["name"]
            record_host: Path | None = None
            if svc.get("record"):
                # Under the **run directory**, with the round diffs and the curve: a
                # recording is evidence the run produced, and the place evidence goes
                # is the record. An earlier version put it under the work root, where
                # nothing ever removed it -- and a per-task directory that no code owns
                # is a directory that grows for the life of the machine.
                record_host = recording_host(recordings_root, task_id, name)
                record_host.mkdir(parents=True, exist_ok=True)
                recordings[name] = record_host

            cmd = ["run", "--detach", "--rm",
                   "--name", label(f"hg-{run_id}-{task_id}-{name}"),
                   # The container's *name* is platform-mangled -- it has to be unique
                   # on the daemon, and several runs may be live at once -- so the name
                   # the harness was told must be attached as an **alias**, or it
                   # resolves to nothing. Measured: without this the harness got
                   # `Name or service not known` for a service that was running and
                   # healthy, so every task that touched it failed for a reason nothing
                   # recorded, and the tasks that only touched the filesystem passed.
                   "--network", net, "--network-alias", name,
                   "--user", f"{os.getuid()}:{os.getgid()}",
                   "--label", f"harnessgrad.run={run_id}",
                   "--label", f"harnessgrad.task={task_id}",
                   "--label", f"harnessgrad.service={name}"]
            # Service resources are opt-in; task ENV caps do not cover sidecars.
            # Reuse the container envelope (including its fixed PID cap) only when
            # the service declares resources, preserving existing uncapped services.
            if "cpus" in svc or "memory_mb" in svc:
                limits = container_mod.limits_from(svc)
                cmd += ["--cpus", str(limits["cpus"]),
                        "--memory", limits["memory"],
                        "--pids-limit", str(limits["pids"])]
            if record_host is not None:
                cmd += ["--mount",
                        f"type=bind,src={record_host},dst={RECORDINGS_ROOT}/{name}"]
            for key, value in sorted(env.items()):
                cmd += ["-e", f"{key}={value}"]
            # No docker socket, and no host tree: a service is infrastructure the
            # harness must not be able to read or kill its way out of (§2.5.8).
            if "entrypoint" in svc:
                cmd += ["--entrypoint", svc["entrypoint"]]
            cmd += [svc["image"], *svc.get("command", [])]
            started = container_mod.docker(*cmd, timeout=180)
            if started.returncode != 0:
                raise ServiceUnavailable(
                    f"service {name!r} did not start: {started.stderr.strip()[:300]}",
                    service=name, stage="start")
            containers[name] = started.stdout.strip()

        running = RunningServices(network=net, containers=containers,
                                  recordings=recordings, services=services)
        for svc in services:
            _await_health(running, svc)
        return running
    except BaseException:
        # Including `KeyboardInterrupt` and `ServiceUnavailable` itself: the one thing
        # that must not happen is a network surviving a failed start and being reused
        # by the next task, where it would look like isolation that is not there.
        RunningServices(network=net, containers=containers,
                        recordings=recordings, services=services).stop()
        raise


def _await_health(running: RunningServices, svc: dict) -> None:
    """Run the service's own health check until it passes or its budget runs out.

    The check runs **inside the service container**, so it sees the service as the
    service sees itself — a host-side port probe would answer a different question and
    would be defeated by a container that binds to localhost only.
    """
    health = svc["health"]
    argv = [str(a) for a in health["argv"]]
    budget = float(health.get("timeout_s", 60))
    deadline = time.monotonic() + budget
    last = ""
    cid = running.containers[svc["name"]]
    while True:
        try:
            proc = container_mod.docker("exec", cid, *argv, timeout=max(10, int(budget)))
        except (subprocess.TimeoutExpired, OSError) as exc:
            last = str(exc)
        else:
            if proc.returncode == 0:
                return
            last = f"exit {proc.returncode}: {(proc.stdout + proc.stderr).strip()[:200]}"
        if time.monotonic() >= deadline:
            raise ServiceUnavailable(
                f"service {svc['name']!r} was not healthy after {budget:g}s "
                f"({last}). This is a dataset-side failure, not a harness result: the "
                f"task is recorded as invalid rather than scored zero.",
                service=svc["name"], stage="health")
        time.sleep(HEALTH_POLL_S)


# --------------------------------------------------------------- mount rules ---

#: Who may see a service's recording. **One place, because the rule is the design.**
#: The harness is absent from this mapping, and that absence is the point: a recording
#: is the only input in this platform the harness cannot even read, and a decision with
#: no code is a decision that gets reversed by accident.
_AUDIENCES = ("harness", "verifier")


def mounts_for(audience: str, services: list[dict],
               recordings: dict[str, Path]) -> list[str]:
    """Docker mount arguments for one audience.

    `harness` → nothing at all. Not read-only, not a copy: the harness cannot see a
    recording, because there is no static version to withhold and a copy written before
    the run would be a copy of nothing.

    `verifier` → every recording, read-only. Read-only because a check that writes into
    the evidence it grades has a verdict that depends on its own leftovers, the same
    reason the task mount is frozen for the check (§2.5.6).
    """
    if audience not in _AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}; known: {list(_AUDIENCES)}")
    if audience == "harness":
        return []
    mounts: list[str] = []
    for svc in services:
        path = recordings.get(svc["name"])
        if path is not None:
            # Same rule as `recording_host`: `--mount` needs an absolute `src`, and a
            # caller may hand this function a relative run tree directly.
            source = Path(path).resolve()
            mounts += ["--mount",
                       f"type=bind,src={source},dst={RECORDINGS_ROOT}/{svc['name']},readonly"]
    return mounts
