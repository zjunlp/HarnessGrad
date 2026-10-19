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
  * ``cpu.max`` and ``memory.max`` from the container's cgroup directory -- what the kernel
    says it applied, which is the value that actually binds;
  * ``Config.User`` / ``Config.WorkingDir`` / ``Config.Entrypoint`` -- the exec defaults.

Declared and observed are compared, and every disagreement is named. A value the probe could
not read is reported as *unobserved*, never as matching: the whole point of the gate is that
"I could not check" must not be spelled "ok".

It changes no admission behaviour by itself. Wiring it in is a separate, reviewed step.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

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

    @property
    def ok(self) -> bool:
        """True only when every declared bound was read back and matched."""
        return not self.problems and not self.unobserved

    def receipt(self) -> dict:
        return {"schema": self.schema, "image": self.image, "image_id": self.image_id,
                "declared": self.declared, "observed": self.observed,
                "ok": self.ok, "problems": self.problems,
                "unobserved": self.unobserved, "covers": list(COVERS),
                "probe_seconds": round(self.probe_seconds, 3)}


def limits_from(spec: dict) -> dict:
    """The docker flags this spec asks for. Mirrors `eval.containers.container.limits_from`."""
    return {"cpus": spec.get("cpus", 2), "pids": 1024,
            "memory": f"{int(spec.get('memory_mb', 4096))}m"}


def _docker_argv(image: str, limits: dict, network: str | None) -> list[str]:
    argv = ["docker", "run", "-d", "--rm",
            "--cpus", str(limits["cpus"]),
            "--memory", limits["memory"],
            "--pids-limit", str(limits["pids"]),
            "--read-only",
            "--tmpfs", "/tmp:rw,size=64m",
            "--network", network or "none",
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
            "image_id": entry.get("Image")}


def _cgroup_dir(pid: int) -> Path | None:
    """The unified (v2) cgroup path of a process, as a path under /sys/fs/cgroup."""
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("0::"):
            return Path("/sys/fs/cgroup") / line[3:].lstrip("/")
    return None


def _read_cgroup(path: Path | None, name: str) -> str | None:
    if path is None:
        return None
    try:
        return (path / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_cpu_max(raw: str | None) -> float | None:
    """`cpu.max` is "quota period" (`max 100000` when unlimited)."""
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if period else None


def _parse_bytes(raw: str | None) -> int | None:
    if not raw:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def observe(image: str, spec: dict, *, network: str | None = None,
            runner=None, timeout_s: float = DEFAULT_TIMEOUT_S) -> Observation:
    """Start one throwaway container under `spec`'s bounds and read the bounds back."""
    runner = runner or (lambda argv, stdin=None: subprocess.run(
        argv, input=stdin, capture_output=True, text=True, timeout=timeout_s + 30))
    limits = limits_from(spec)
    started = time.monotonic()
    container = None
    try:
        container = _exec(runner, _docker_argv(image, limits, network))
        fields = _inspect_fields(runner, container)
        cgroup = _cgroup_dir(int(fields["pid"])) if fields.get("pid") else None
        cpu_max = _read_cgroup(cgroup, "cpu.max")
        memory_max = _read_cgroup(cgroup, "memory.max")
        pids_max = _read_cgroup(cgroup, "pids.max")
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
                "rootfs_readonly": True,
                "user": spec.get("user"), "workdir": spec.get("workdir")}
    observed = {
        "cpus_docker": (fields["nano_cpus"] or 0) / 1e9 or None,
        "cpus_cgroup": _parse_cpu_max(cpu_max),
        "memory_mb_docker": (fields["memory"] or 0) / 2**20 or None,
        "memory_mb_cgroup": ((_parse_bytes(memory_max) or 0) / 2**20) or None,
        "pids_docker": fields["pids"],
        "pids_cgroup": _parse_bytes(pids_max),
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
    compare("cpus (cgroup cpu.max)", declared["cpus"], observed["cpus_cgroup"], tolerance=0.01)
    compare("memory_mb (docker)", declared["memory_mb"], observed["memory_mb_docker"],
            tolerance=1.0)
    compare("memory_mb (cgroup memory.max)", declared["memory_mb"],
            observed["memory_mb_cgroup"], tolerance=1.0)
    compare("pids (docker)", declared["pids"], observed["pids_docker"])
    compare("pids (cgroup pids.max)", declared["pids"], observed["pids_cgroup"])
    compare("network", declared["network"], observed["network"])
    compare("rootfs_readonly", declared["rootfs_readonly"], observed["rootfs_readonly"])
    # exec_defaults: an empty User ("") in docker means root, which is what the runner asks
    # for when the spec names no user; normalise it so an absent field is not a mismatch.
    if declared["user"] not in (None, "") or observed["user"] not in (None, ""):
        compare("user", declared["user"] or "", observed["user"] or "")

    return Observation(schema=SCHEMA, image=image, image_id=fields.get("image_id"),
                       declared=declared, observed=observed, problems=problems,
                       unobserved=unobserved, probe_seconds=time.monotonic() - started)


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
