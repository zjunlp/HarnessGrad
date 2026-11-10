#!/usr/bin/env python3
"""Observe, from the runtime, the resource and network bounds a container really got.

Why this exists
---------------
`eval/run/runner.py: _refuse_unobserved_policies` refuses any task whose environment
declares ``cpus``, ``memory_mb``, ``resources``, ``filesystems``, ``network_policy``,
``artifact_policy``, ``exec_defaults`` or ``state: container``, unless this process can show
it *applies and observed* those bounds. That is why AppWorld stops at admission with
``backend_observation_unavailable`` -- measured 2026-10-18:

    ! 07b42fd_1: invalid for dataset 'appworld' at stage 'admission'
                 -- backend_observation_unavailable
    policy_fields: env.cpus, env.exec_defaults, env.memory_mb, env.network

The bounds *are* applied: `eval/containers/container.py` passes ``--cpus``/``--memory``/
``--pids-limit`` on every start, and `limits_from()` derives them from the task's own spec.
What was missing is the observation half: nobody read the effective values back from Docker
and from the kernel's cgroup files, so the platform had nothing to put in the admission
receipt.

What it does
------------
Starts one throwaway container with the *same* flags the runner will use, then reads:

  * ``HostConfig.NanoCpus`` / ``Memory`` / ``PidsLimit`` / ``NetworkMode`` / ``ReadonlyRootfs``
    from ``docker inspect`` -- what Docker says it applied;
  * v2 ``cpu.max`` / ``memory.max`` or v1 CPU quota/period and memory-limit files
    from the container's controller mounts -- what the kernel says it applied;
  * ``Config.User`` / ``Config.WorkingDir`` / ``Config.Entrypoint`` -- the exec defaults.

Declared and observed are compared, and every disagreement is named. A value the probe could
not read is reported as *unobserved*, never as matching: the whole point of the gate is that
"I could not check" must not be spelled "ok".

It changes no admission behaviour by itself: the receipt only admits a task when the
caller hands it to `eval/run/runner.py`'s `_refuse_unobserved_policies`, and the one
producer is `_observations_for` there. That caller derives the probe's image, bounds and
rootfs mode from the **launcher** (`eval/containers/container.py:envelope`) rather than from
the dataset's declaration, and refuses a receipt when the two disagree; `observe_for`
resolves the declared `network` policy word the way the runner will, because `services` is
a policy and not a docker network name.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import eval.service.netpool as netpool
from eval.containers import cgroups

SCHEMA = "harnessgrad.container-observation.v1"
#: The policy fields this probe measures back. `_refuse_unobserved_policies` admits only these,
#: and only when every field it was asked about is covered -- an observation that proves the
#: CPU and memory bounds says nothing about `filesystems` or `artifact_policy`.
COVERS = ("env.cpus", "env.memory_mb", "env.exec_defaults", "env.network",
          "env.state=container")
#: Long enough for a cold image pull-free start, short enough that a stuck daemon fails.
DEFAULT_TIMEOUT_S = 120


class ObservationError(RuntimeError):
    """The probe itself could not run; distinct from "the bounds did not match"."""


@dataclass
class Observation:
    schema: str
    image: str
    declared: dict
    observed: dict
    problems: list[str] = field(default_factory=list)
    unobserved: list[str] = field(default_factory=list)
    probe_seconds: float = 0.0
    image_id: str | None = None
    launch_mode: str | None = None

    @property
    def ok(self) -> bool:
        """True only when every declared bound was read back and matched."""
        return not self.problems and not self.unobserved

    def receipt(self) -> dict:
        return {**({"launch_mode": self.launch_mode} if self.launch_mode else {}),
                "schema": self.schema, "image": self.image, "image_id": self.image_id,
                "declared": self.declared, "observed": self.observed,
                "ok": self.ok, "problems": self.problems,
                "unobserved": self.unobserved, "covers": list(COVERS),
                "probe_seconds": round(self.probe_seconds, 3)}


def limits_from(spec: dict) -> dict:
    """The docker flags this spec asks for. Mirrors `eval.containers.container.limits_from`."""
    return {"cpus": spec.get("cpus", 2), "pids": 1024,
            "memory": f"{int(spec.get('memory_mb', 4096))}m"}


def _docker_argv(image: str, limits: dict, network: str | None,
                 *, read_only: bool = True) -> list[str]:
    argv = ["docker", "run", "-d", "--rm",
            "--cpus", str(limits["cpus"]),
            "--memory", limits["memory"],
            "--pids-limit", str(limits["pids"])]
    if read_only:
        # Whether the rootfs is read-only is a property of the container being described,
        # not of the probe: `container_mod.run` starts the harness's container read-only,
        # and a launch that does not must not be described by a receipt that says it did.
        # Claiming a read-only rootfs while starting a writable one would put a value in
        # the receipt that nothing measured.
        argv += ["--read-only", "--tmpfs", "/tmp:rw,size=64m"]
    argv += ["--network", network or "none",
             "--entrypoint", "sleep", image, str(DEFAULT_TIMEOUT_S)]
    return argv


def _exec(runner, argv: list[str], *, stdin: bytes | None = None):
    proc = runner(argv, stdin)
    if proc.returncode != 0:
        raise ObservationError(f"{shlex.join(argv[:3])} failed: "
                               f"{(proc.stderr or '').strip()[:300]}")
    return proc.stdout.strip()


def _inspect_fields(runner, container: str) -> dict:
    raw = _exec(runner, ["docker", "inspect", "--type", "container", container])
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise ObservationError("docker inspect returned no container")
    entry = data[0]
    host = entry.get("HostConfig") or {}
    config = entry.get("Config") or {}
    return {"nano_cpus": host.get("NanoCpus"), "memory": host.get("Memory"),
            "pids": host.get("PidsLimit"), "network": host.get("NetworkMode"),
            "rootfs_readonly": (host.get("ReadonlyRootfs") is True),
            "user": config.get("User"), "workdir": config.get("WorkingDir"),
            "entrypoint": config.get("Entrypoint"), "pid": (entry.get("State") or {}).get("Pid"),
            "image_id": entry.get("Image"), "healthcheck": config.get("Healthcheck"),
            "networks": sorted("bridge" if name == "bridge" else row.get("NetworkID", name)
                               for name, row in ((entry.get("NetworkSettings") or {}).get("Networks") or {}).items())}


def _cgroup_dir(pid: int) -> cgroups.Layout | None:
    """Resolve host-visible v1 controller mounts or the unified v2 mount."""
    return cgroups.resolve(pid)


def _default_runner(timeout_s: float):
    def run(argv, stdin=None):
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, errors="replace",
                              timeout=timeout_s + 30)
    return run


def observe(image: str, spec: dict, *, network: str | None = None,
            runner=None, timeout_s: float = DEFAULT_TIMEOUT_S,
            read_only: bool = True, container_state: bool = False,
            attach_bridge: bool = False, owner_run_id: str = "") -> Observation:
    """Start one throwaway container under `spec`'s bounds and read the bounds back."""
    runner = runner or _default_runner(timeout_s)
    if container_state and read_only:
        raise ObservationError("container-state observation requires the writable launcher rootfs")
    limits = limits_from(spec)
    started = time.monotonic()
    container = None
    try:
        if container_state:
            # Use the actual state launcher's create/start policy: inherited image
            # entrypoint/user/workdir, writable rootfs, resource flags and idle command.
            # A readonly mount-run probe cannot authorize snapshot execution.
            from eval.containers import container as backend
            if not backend.looks_pinned(image):
                raise ObservationError("container-state observation requires a pinned image")
            container = "hg-observe-state-" + secrets.token_hex(16)
            try:
                backend.create(container, image=image, limits=limits, network=network or "none",
                               run_id=owner_run_id or "observation", task_id="container-observation")
                if attach_bridge:
                    backend.network_connect(container, "bridge")
                backend.start(container)
            except backend.ContainerUnavailable as exc:
                raise ObservationError(str(exc)) from exc
        else:
            container = _exec(runner, _docker_argv(image, limits, network,
                                                   read_only=read_only))
        fields = _inspect_fields(runner, container)
        image_defaults = (json.loads(_exec(runner, ["docker", "image", "inspect", image]))[0].get("Config") or {}) if container_state else None
        cgroup_error = None
        try:
            cgroup = _cgroup_dir(int(fields["pid"])) if fields.get("pid") else None
        except (OSError, ValueError) as exc:
            cgroup, cgroup_error = None, str(exc)
        resource = cgroups.limits(cgroup) if cgroup is not None else {}
        cpu_max = resource.get('raw', {}).get('cpu.max')
        memory_max = resource.get('raw', {}).get('memory.max')
        pids_max = resource.get('raw', {}).get('pids.max')
    finally:
        if container:
            try:                                   # cleanup must never mask a probe failure
                runner(["docker", "rm", "-f", container], None)
            except Exception:                      # noqa: BLE001
                pass

    # `network` is the docker network *name* the runner will use, not the spec's policy word:
    # `services` is resolved to a concrete network at start, and probing a different network
    # than the task gets would observe the wrong thing.
    declared = {"cpus": float(limits["cpus"]), "memory_mb": int(spec.get("memory_mb", 4096)),
                "pids": limits["pids"], "network": network or "none",
                "rootfs_readonly": bool(read_only),
                "user": spec.get("user"), "workdir": spec.get("workdir")}
    observed = {
        "cpus_docker": (fields["nano_cpus"] or 0) / 1e9 or None,
        "cpus_cgroup": resource["cpu_millis"] / 1000 if resource.get("cpu_millis") is not None else None,
        "memory_mb_docker": (fields["memory"] or 0) / 2**20 or None,
        "memory_mb_cgroup": (resource.get("memory_bytes") or 0) / 2**20 or None,
        "pids_docker": fields["pids"],
        "pids_cgroup": resource.get("pids"),
        "resource_backend": resource.get("backend"),
        "cgroup_evidence": resource,
        "cgroup_error": cgroup_error,
        "network": fields["network"],
        "rootfs_readonly": fields["rootfs_readonly"],
        "user": fields["user"],
        "workdir": fields["workdir"],
        "entrypoint": fields["entrypoint"],
        "cpu_max_raw": cpu_max, "memory_max_raw": memory_max,
    }
    problems: list[str] = []
    unobserved: list[str] = []

    def compare(name: str, want, got, *, tolerance: float = 0.0) -> None:
        if got is None:
            unobserved.append(f"{name}: could not be read back")
        elif isinstance(want, (int, float)) and isinstance(got, (int, float)):
            if abs(float(want) - float(got)) > tolerance:
                problems.append(f"{name}: declared {want}, observed {got}")
        elif want != got:
            problems.append(f"{name}: declared {want!r}, observed {got!r}")

    compare("cpus (docker)", declared["cpus"], observed["cpus_docker"], tolerance=1e-9)
    cpu_file = "cpu.cfs_quota_us/cpu.cfs_period_us" if resource.get("backend") == "cgroup-v1" else "cpu.max"
    memory_file = "memory.limit_in_bytes" if resource.get("backend") == "cgroup-v1" else "memory.max"
    compare(f"cpus (cgroup {cpu_file})", declared["cpus"], observed["cpus_cgroup"], tolerance=0.01)
    compare("memory_mb (docker)", declared["memory_mb"], observed["memory_mb_docker"],
            tolerance=1.0)
    compare(f"memory_mb (cgroup {memory_file})", declared["memory_mb"],
            observed["memory_mb_cgroup"], tolerance=1.0)
    compare("pids (docker)", declared["pids"], observed["pids_docker"])
    compare("pids (cgroup pids.max)", declared["pids"], observed["pids_cgroup"])
    compare("network", declared["network"], observed["network"])
    compare("rootfs_readonly", declared["rootfs_readonly"], observed["rootfs_readonly"])
    # exec_defaults: an empty User ("") in docker means root, which is what the runner asks
    # for when the spec names no user; normalise it so an absent field is not a mismatch.
    if not container_state and (declared["user"] not in (None, "") or observed["user"] not in (None, "")):
        compare("user", declared["user"] or "", observed["user"] or "")

    if container_state:
        # The real state create inherits these image defaults. Actor exec later
        # supplies its own task cwd; this does not authorize arbitrary user/cwd overrides.
        for field, key in (("user", "User"), ("workdir", "WorkingDir"),
                           ("entrypoint", "Entrypoint"), ("healthcheck", "Healthcheck")):
            expected_default = image_defaults.get(key)
            actual_default = fields[field]
            # Docker image JSON may omit empty string defaults that container
            # inspect spells "". Both mean root user / default root working dir.
            if key in ("User", "WorkingDir"):
                expected_default, actual_default = expected_default or "", actual_default or ""
            if actual_default != expected_default:
                problems.append(f"image {field}: declared {expected_default!r}, observed {actual_default!r}")
        observed["image_defaults"] = {key: image_defaults.get(key) for key in
                                      ("User", "WorkingDir", "Entrypoint", "Healthcheck")}
        # Primary internal network plus the optional bridge attachment must both
        # survive Docker's launcher; no inferred permission from the policy word.
        actual = set(fields["networks"])
        expected = {network or "none"} | ({"bridge"} if attach_bridge else set())
        compare("network attachments", sorted(expected), sorted(actual))
        observed["networks"] = fields["networks"]

    return Observation(schema=SCHEMA, image=image, image_id=fields.get("image_id"),
                       launch_mode="container-state" if container_state else None,
                       declared=declared, observed=observed, problems=problems,
                       unobserved=unobserved, probe_seconds=time.monotonic() - started)


def observe_for(image: str, spec: dict, *, network: str | None, run_id: str = "",
                runner=None, timeout_s: float = DEFAULT_TIMEOUT_S,
                read_only: bool = True, container_state: bool = False) -> Observation:
    """`observe` on the network the task will actually get, for an `exec` environment.

    `network` here is the declared **policy** (INTERFACE.md §2.5.2): `none`, `services` or
    `bridge`. The policy word is not a docker network name, so it cannot be handed to
    `docker run` -- `services` is resolved per task to a private `--internal` network
    (`eval/service/services.py:create_network`) that does not exist yet when admission is
    decided. This creates one of the same shape, on the same flags, labelled with the same
    run so `services.reap` can find it after a crash, probes on it, and removes it in a
    `finally`. Probing on `--network none` instead would put `NetworkMode: none` in the
    receipt for a task that is about to get an internal network: true about a container
    nobody runs.

    `read_only` is the **launcher's** value for the container being described
    (`container_mod.run` defaults it to `True`; a task whose state is the container's
    filesystem uses False). It is a parameter because the rootfs in the receipt has
    to be the rootfs the harness saw, and only the caller knows which launcher ran.

    Container-state probes call the same create/start functions as the actual
    snapshot launcher, retaining image entrypoint/user/workdir/healthcheck defaults,
    writable rootfs and its always-internal primary network. Declared bridge adds
    the same secondary attachment. This executes the selected pinned image just
    as the actual launcher would; it never pulls or substitutes a different image.

    The probe's network is allocated exactly like a task's (`netpool.allocate`, key
    `probe-<run_id>`) and created with an explicit `--subnet`, so the probe cannot become
    the thing that exhausts the daemon's default pool -- it is created at admission, when
    the first tasks are starting, which is the worst moment to spend a `/16`.
    """
    runner = runner or _default_runner(timeout_s)
    if container_state and network not in ("none", "services", "bridge", None):
        raise ObservationError("unsupported container-state network policy")
    internal_network = network == "services" or container_state
    probe_network: str | None = None
    if container_state:
        from eval.service import services
        from eval.containers import container as backend
        if not backend.looks_pinned(image):
            raise ObservationError("container-state observation requires a pinned image")
        # Same allocator/internal-network mechanism as the real state launcher;
        # retain the unique returned Docker ID and remove only that acquisition.
        try:
            probe_network = services.create_network(run_id or "observation",
                                                    "container-observation-" + secrets.token_hex(8))
        except services.ServiceUnavailable as exc:
            raise ObservationError(str(exc)) from exc
    elif internal_network:
        probe_network = _probe_network_name(image, spec, run_id)
        _create_probe_network(runner, probe_network, run_id)
    else:
        probe_network = network
    try:
        if container_state:
            info = json.loads(_exec(runner, ["docker", "network", "inspect", probe_network]))
            if not info or info[0].get("Internal") is not True:
                raise ObservationError("container-state primary network was not observed internal")
        result = observe(image, spec, network=probe_network, runner=runner,
                         timeout_s=timeout_s, read_only=read_only,
                         **({"container_state": True, "attach_bridge": network == "bridge", "owner_run_id": run_id}
                            if container_state else {}))
        if container_state:
            result.declared.update(state="container", network_policy=network or "none")
            result.observed["network_internal"] = True
        return result
    finally:
        if container_state and probe_network is not None:
            failures = services.remove_network(probe_network)
            if failures:
                raise ObservationError("container-state probe network cleanup failed: " + "; ".join(failures))
        elif internal_network and probe_network is not None:
            try:                     # cleanup must never mask a probe failure
                runner(["docker", "network", "rm", probe_network], None)
            except Exception:        # noqa: BLE001
                pass


def _create_probe_network(runner, name: str, run_id: str) -> str:
    """Create the probe's throwaway network on an allocated `/24`. Returns the subnet.

    The same two rules as `services.create_network`, for the same reason: an explicit
    `--subnet` on every create (a probe network drawn from the default pool is the next
    thing that can exhaust it -- one per container shape per process, created at admission
    exactly when the first task networks are appearing), and the allocator's next candidate
    on a collision. The key is `probe-<run_id>` rather than the run's own, so a probe and
    the tasks it observes never ask for the same subnet first.
    """
    try:
        candidates = netpool.allocate(f"probe-{run_id or 'run'}", name)
    except netpool.NetPoolError as exc:
        # An unusable base is a host configuration problem, and the allocator's message
        # already names the variable; keep the probe's caller on `ObservationError` so a
        # misconfigured base is a refused admission, not a ValueError out of the runner.
        raise ObservationError(
            f"cannot allocate the observation network {name}: {exc}") from exc
    run_label = f"harnessgrad.run={run_id or 'run'}"
    last = ""
    for subnet in candidates:
        created = runner(["docker", "network", "create", "--internal", name,
                          "--subnet", subnet,
                          "--label", run_label,
                          "--label", f"{netpool.SUBNET_LABEL}={subnet}"], None)
        if created.returncode == 0:
            return subnet
        stderr = (created.stderr or "").strip()
        if netpool.network_exists(stderr, name):
            existing = _existing_network_subnet(runner, name)
            if existing == subnet:
                # A probe killed mid-flight leaves this name and subnet behind; the digest
                # in the name is a function of the bounds, so it asks for the same subnet
                # again. Reuse rather than refuse admission for a network nobody is using.
                return subnet
            raise ObservationError(
                f"network {name} already exists with subnet "
                f"{existing or 'unknown'}, but the probe asked for {subnet}. Remove the "
                f"leftover with `docker network rm {name}` and re-run admission.")
        if netpool.subnet_taken(stderr):
            last = stderr.splitlines()[-1][:160] if stderr else ""
            continue
        raise ObservationError(
            f"could not create the observation network {name}: {stderr[:300]}")
    raise ObservationError(
        f"could not create the observation network {name} on any "
        f"/{netpool.SUBNET_PREFIX} of the configured base {candidates.base} "
        f"({candidates.count} tried; last: {last or 'none'}). Set "
        f"{netpool.BASE_ENV} to an unused IPv4 CIDR of /{netpool.SUBNET_PREFIX} or larger, "
        f"or check the host with `python3 tools/network_preflight.py --networks 40`.")


def _existing_network_subnet(runner, name: str) -> str | None:
    """The subnet recorded on a network, read through the caller's docker runner."""
    proc = runner(["docker", "network", "inspect", name], None)
    if proc.returncode != 0:
        return None
    return netpool.subnet_from_network_inspect(proc.stdout or "")


def _probe_network_name(image: str, spec: dict, run_id: str) -> str:
    """A legal docker network name, unique per run and per observed spec.

    `hg-observe-<run>-<8 hex>` rather than `hg-<run>-...` so it can never be confused with
    a task's own network (`services.create_network`), which a task still needs at the same
    moment. The digest is over the bounds being observed, so two environments that ask
    for the same container share one name and cannot collide with one that asks for
    another.
    """
    run = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id or "run")
    bounds = json.dumps([image, spec.get("cpus"), spec.get("memory_mb")], sort_keys=True)
    digest = hashlib.sha256(bounds.encode()).hexdigest()[:8]
    return f"hg-observe-{run}-{digest}"


if __name__ == "__main__":                         # a manual probe, not a test
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--cpus", type=float, default=2)
    ap.add_argument("--memory-mb", type=int, default=4096)
    ap.add_argument("--network", default=None)
    a = ap.parse_args()
    print(json.dumps(observe(a.image, {"cpus": a.cpus, "memory_mb": a.memory_mb},
                             network=a.network).receipt(), indent=1))
