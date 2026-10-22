"""Manifest-bound, no-egress admission for the inherited cached pilot.

This is a local control boundary, not a benchmark runner. Persistent leases are
cooperative accounting, not filesystem quotas. Unknown cleanup retains capacity.
Official checks need a pinned offline closure and an artifact-only trusted runtime;
an actor snapshot cannot supply the grader's shell, interpreter or dependencies.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

INHERITED_SHA256 = "b40f0f0a2636d90db0846fd70ab208ffc3e83e1f4e24c80918f6ab1324153879"
PILOT = (
    "git-multibranch", "mailman", "cobol-modernization", "git-leak-recovery",
    "build-pmars", "financial-document-processor",
)
IMAGE_IDS = (
    "5bc449921c138295f6ddd8dca2977319fee2ce523811dbe56cec4ca663385fb5",
    "04fac73db7e2b4f49d43f09fbc2266a94d70131f07efa1393d370abc059f4094",
    "593ab9df3d83f771e927888b2d9436b380cdf7ec4d0af2a9f0802d541edebba0",
    "62160e522a4238b8bcd522411ea97b5b200f811e5899d34947e66809bef7e586",
    "57a82706f7491e0b2d1ff7e927e97ae5339892dd09c47ac43290b4ba1f5abc6f",
    "ef6c9cfaaf14cdd200163008a188d5baa9f626f3bfb8d3e7d6f30c08518e6251",
)


class AdmissionError(RuntimeError):
    pass


def looks_pinned(image: str) -> bool:
    return isinstance(image, str) and bool(re.fullmatch(
        r"(?:[^\s@]+@)?sha256:[a-f0-9]{64}", image))


def _quantities(values: dict, keys: set[str], label: str) -> None:
    if (not isinstance(values, dict) or not keys <= values.keys()
            or any(type(values[k]) is not int or values[k] < 0 for k in keys)):
        raise AdmissionError(f"invalid {label} quantities")


def _devices(devices: dict) -> None:
    if not isinstance(devices, dict) or not devices:
        raise AdmissionError("nonempty device observations required")
    for dev, row in devices.items():
        if not isinstance(dev, str) or not dev:
            raise AdmissionError("invalid observed device identity")
        _quantities(row, {"free_bytes", "free_inodes"}, "device observation")
        if "paths" in row and (not isinstance(row["paths"], list)
                or not row["paths"] or any(not isinstance(p, str) or not p for p in row["paths"])):
            raise AdmissionError("invalid observed device paths")


def _validate_ledger(data: dict) -> None:
    if (not isinstance(data, dict) or data.get("schema") != "harnessgrad.resource-ledger.v1"
            or not isinstance(data.get("leases"), dict)):
        raise AdmissionError("invalid reservation ledger")
    for row in data["leases"].values():
        if not isinstance(row, dict) or row.get("state") not in ("active", "quarantined", "released"):
            raise AdmissionError("unknown lease state")
        if (not isinstance(row.get("devices"), dict) or not row["devices"]
                or not isinstance(row.get("resources"), dict)
                or set(row["resources"]) != {"cpus", "memory_bytes", "pids"}
                or not isinstance(row.get("manifest_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", row["manifest_sha256"])
                or not isinstance(row.get("owned"), list)):
            raise AdmissionError("incomplete lease allocation")
        _quantities(row["resources"], {"cpus", "memory_bytes", "pids"}, "lease")
        for dev, allocation in row["devices"].items():
            if not isinstance(dev, str) or not dev or not isinstance(allocation, dict) or set(allocation) != {"bytes", "inodes"}:
                raise AdmissionError("malformed lease allocation")
            _quantities(allocation, {"bytes", "inodes"}, "lease")
        _devices(row.get("observed_devices"))
        for owned in row["owned"]:
            if (not isinstance(owned, dict) or not isinstance(owned.get("name"), str)
                    or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", owned["name"])
                    or (owned.get("id") is not None and (not isinstance(owned["id"], str)
                        or not re.fullmatch(r"[a-f0-9]{64}", owned["id"])))
                    or owned.get("task_id") not in PILOT):
                raise AdmissionError("invalid owned container record")


def atomic_json(path: Path, value) -> None:
    """Durable replacement; a crash never turns a partial ledger into free capacity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x") as f:
            os.chmod(tmp, 0o600)
            json.dump(value, f, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def load_manifest(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if (data.get("schema") != "harnessgrad.cached-pilot-admission.v1"
            or data.get("inherited_pilot_sha256") != INHERITED_SHA256
            or data.get("task_ids") != list(PILOT) or data.get("jobs") != 1
            or data.get("benchmark_enabled") is not False
            or data.get("allowed_execution") != "synthetic_local_runtime_controls"):
        raise AdmissionError("manifest does not bind the inherited six-task control scope")
    if data.get("budgets") != dict(pulls=0, builds=0, model_calls=0,
                                   benchmark_trials=0, heldout_reads=0):
        raise AdmissionError("manifest permits prohibited work")
    tasks = data.get("tasks", [])
    if [t.get("task_id") for t in tasks] != list(PILOT):
        raise AdmissionError("task rows differ from frozen scope")
    for t, digest in zip(tasks, IMAGE_IDS):
        image = t.get("image", "")
        prefix = "alexgshaw/" if t["task_id"] == "build-pmars" else "docker.1panel.live/alexgshaw/"
        if (t.get("image_id") != "sha256:" + digest
                or image != prefix + t["task_id"] + "@sha256:" + digest
                or hashlib.sha256(t["task_id"].encode()).digest()[0] % 3 == 0):
            raise AdmissionError("task cache pin or train membership changed")
        if t.get("scorer", {}).get("network") != "none":
            raise AdmissionError("scorer egress is not authorized")
    runtime = data.get("runtime", {})
    expected = dict(network="none", pull="never", read_only_root=True,
                    memory_bytes=256 * 1024**2, cpus=1, pids=128,
                    tmpfs_bytes=16 * 1024**2, tmpfs_inodes=4096, timeout_s=30)
    if runtime != expected:
        raise AdmissionError("runtime envelope changed")
    expected_reserve = dict(growth_bytes_per_device=4 * 1024**3,
                            floor_bytes_per_device=8 * 1024**3,
                            inodes_per_device=100000, floor_inodes_per_device=10000,
                            cpus=2, memory_bytes=9 * 1024**3, pids=2048)
    if data.get("reservation") != expected_reserve:
        raise AdmissionError("resource reservation changed")
    return data, hashlib.sha256(raw).hexdigest()


def benchmark_blockers(manifest: dict) -> list[str]:
    """Do not upgrade policy declarations or cached images into scorer evidence."""
    blockers = []
    for task in manifest["tasks"]:
        for field in ("dependency_closure_verified", "trusted_runtime_verified",
                      "state_export_contract_verified"):
            # The production adapter supplies no pinned artifact contract or official
            # runtime receipt. Neither manifest booleans nor local synthetic receipts
            # can close these task-specific evidence gaps.
            blockers.append(f"{task['task_id']}: {field}")
    # There is intentionally no boolean override for these implementation gaps.
    blockers += ["writable benchmark disk/inode quotas unavailable in this control boundary",
                 "official scorer execution is outside the admitted synthetic scope"]
    return blockers


def filesystem_capacity(paths: list[Path]) -> dict[str, dict]:
    """Group Docker, containerd and evidence by actual device; never sum aliases."""
    result = {}
    for path in paths:
        path = path.resolve(strict=True)
        stat = os.stat(path)
        fs = os.statvfs(path)
        device = str(stat.st_dev)
        free = fs.f_bavail * fs.f_frsize
        inodes = fs.f_favail
        if device in result:
            row = result[device]
            row["paths"].append(str(path))
            row["free_bytes"] = min(row["free_bytes"], free)
            row["free_inodes"] = min(row["free_inodes"], inodes)
        else:
            result[device] = dict(paths=[str(path)], free_bytes=free, free_inodes=inodes)
    return result


def host_capacity() -> dict:
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        mem[key] = int(value.split()[0]) * 1024
    pid_max = int(Path("/proc/sys/kernel/pid_max").read_text())
    processes = sum(p.name.isdigit() for p in Path("/proc").iterdir())
    return dict(cpus=len(os.sched_getaffinity(0)), memory_bytes=mem["MemAvailable"],
                pids=max(0, pid_max - processes))


class ReservationLedger:
    """One shared ledger per workspace/daemon; never expire crash leases by time."""
    def __init__(self, path: Path):
        self.path = path.resolve()

    @contextlib.contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(self.path.read_text()) if self.path.exists() else {
                    "schema": "harnessgrad.resource-ledger.v1", "leases": {}}
                _validate_ledger(data)
                yield data
            except (ValueError, KeyError, TypeError) as exc:
                raise AdmissionError("corrupt reservation ledger; capacity is unknown") from exc
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def acquire(self, manifest_hash: str, policy: dict, devices: dict, host: dict) -> str:
        if not isinstance(manifest_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", manifest_hash):
            raise AdmissionError("invalid manifest hash")
        _quantities(policy, {"growth_bytes_per_device", "floor_bytes_per_device",
                            "inodes_per_device", "floor_inodes_per_device",
                            "cpus", "memory_bytes", "pids"}, "reservation")
        _devices(devices)
        _quantities(host, {"cpus", "memory_bytes", "pids"}, "host observation")
        with self.locked() as data:
            held = [r for r in data["leases"].values() if r["state"] != "released"]
            allocations = {}
            for dev, available in devices.items():
                used_bytes = sum(r["devices"].get(dev, {}).get("bytes", 0) for r in held)
                used_inodes = sum(r["devices"].get(dev, {}).get("inodes", 0) for r in held)
                if (available["free_bytes"] - used_bytes < policy["growth_bytes_per_device"] + policy["floor_bytes_per_device"]
                        or available["free_inodes"] - used_inodes < policy["inodes_per_device"] + policy["floor_inodes_per_device"]):
                    raise AdmissionError(f"disk/inode headroom insufficient on device {dev}")
                allocations[dev] = dict(bytes=policy["growth_bytes_per_device"], inodes=policy["inodes_per_device"])
            resources = {key: policy[key] for key in ("cpus", "memory_bytes", "pids")}
            for key, request in resources.items():
                if host[key] - sum(r["resources"][key] for r in held) < request:
                    raise AdmissionError(f"host {key} capacity insufficient")
            lease = uuid.uuid4().hex
            data["leases"][lease] = dict(state="active", manifest_sha256=manifest_hash,
                                        resources=resources, devices=allocations,
                                        observed_devices=devices, created_at=time.time(),
                                        owned=[], cleanup_errors=[])
            _validate_ledger(data)
            atomic_json(self.path, data)
            return lease

    def update(self, lease: str, **fields) -> dict:
        with self.locked() as data:
            row = data["leases"][lease]
            if row["state"] == "released":
                raise AdmissionError("released lease is immutable")
            if row["state"] == "quarantined" and fields.get("state") == "active":
                raise AdmissionError("quarantined capacity requires verified cleanup")
            row.update(fields)
            _validate_ledger(data)
            atomic_json(self.path, data)
            return row.copy()

    def get(self, lease: str) -> dict:
        with self.locked() as data:
            return json.loads(json.dumps(data["leases"][lease]))


class LocalDocker:
    """No registry, context, proxy or provider credential inheritance."""
    def call(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        return subprocess.run(["docker", "--host", "unix:///var/run/docker.sock", *args],
                              env=env, capture_output=True, text=True, errors="replace", timeout=timeout)

    def inspect_image(self, task: dict) -> dict:
        _task_pin(task)
        info = self._image_info(task["image"], task["image_id"])
        # A base image can carry inherited volumes or credentials; deny before create.
        config = info.get("Config", {})
        if config.get("Volumes"):
            raise AdmissionError("image declares unmanaged volumes")
        return dict(image_id=info["Id"], image=task["image"],
                    workdir=config.get("WorkingDir"), size_bytes=info.get("Size"))

    def _inspect(self, kind: str, reference: str) -> dict | None:
        try:
            proc = self.call(kind, "inspect", reference)
            if proc.returncode:
                if kind == "container" and exact_absence(proc, kind, reference):
                    return None
                raise AdmissionError(f"{kind} absence unproven: daemon query failed")
            data = json.loads(proc.stdout)
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
                raise ValueError("expected one inspected object")
            return data[0]
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
            raise AdmissionError(f"{kind} inspection failed; identity/absence unknown") from exc

    def _image_info(self, image: str, expected_id: str | None = None) -> dict:
        if not looks_pinned(image):
            raise AdmissionError("image must have an immutable local pin")
        if expected_id is not None and (not isinstance(expected_id, str)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_id)):
            raise AdmissionError("invalid expected image ID")
        info = self._inspect("image", image)
        inspected_id = info.get("Id")
        if not isinstance(inspected_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", inspected_id):
            raise AdmissionError("invalid inspected image ID")
        if ((image.startswith("sha256:") and inspected_id != image)
                or (expected_id is not None and inspected_id != expected_id)
                or ("@" in image and image not in info.get("RepoDigests", []))):
            raise AdmissionError("cached image identity mismatch")
        return info

    def require_local_image(self, image: str, expected_id: str | None = None) -> str:
        """Reinspect pins without resolving tags or pulling; bare IDs compare themselves."""
        return self._image_info(image, expected_id)["Id"]

    def inspect_container(self, reference: str) -> dict | None:
        return self._inspect("container", reference)


def exact_absence(proc: subprocess.CompletedProcess, kind: str, reference: str) -> bool:
    """Only a complete daemon not-found reply naming the exact queried object counts."""
    if proc.returncode != 1 or proc.stdout.strip() not in ("", "[]"):
        return False
    return proc.stderr.strip() in {
        f"Error response from daemon: No such {kind}: {reference}",
        f"Error: No such {kind}: {reference}",
        f"Error: No such object: {reference}",
    }


def _task_pin(task: dict) -> None:
    if (not isinstance(task, dict) or task.get("task_id") not in PILOT
            or not looks_pinned(task.get("image"))
            or not isinstance(task.get("image_id"), str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", task["image_id"])):
        raise AdmissionError("control image outside inherited pilot")


TMPFS = "rw,nosuid,nodev,noexec,size=16m,nr_inodes=4096,mode=1777"
LOG_CONFIG = dict(Type="local", Config={"max-size": "1m", "max-file": "1", "compress": "false"})


def _mount_plan(mounts: tuple[str, ...]) -> list[dict]:
    # Retain the draft's --mount pair API, but admit only its sealed artifact bind.
    if not isinstance(mounts, tuple) or len(mounts) not in (0, 2):
        raise AdmissionError("only validated artifact mount pairs are allowed")
    if not mounts:
        return []
    if mounts[0] != "--mount" or not isinstance(mounts[1], str):
        raise AdmissionError("unrelated Docker flags are not mounts")
    fields = mounts[1].split(",")
    if (len(fields) != 4 or fields[0] != "type=bind" or not fields[1].startswith("src=/")
            or fields[2:] != ["dst=/control-input", "readonly"]):
        raise AdmissionError("only a read-only artifact bind is allowed")
    source = fields[1][4:]
    if (any(c in source for c in "\n\r\x00") or source != os.path.normpath(source)
            or source.startswith("//")):
        raise AdmissionError("invalid artifact mount source")
    return [dict(Type="bind", Source=source, Destination="/control-input", RW=False)]


def _runtime_envelope(info: dict, task: dict, owned: dict, owner: str, mounts: tuple[str, ...]) -> None:
    """Fail before start if the daemon did not create the requested inert boundary."""
    try:
        config, host = info["Config"], info["HostConfig"]
        labels = config["Labels"]
        if (info["Id"] != owned["id"] or info["Name"] != "/" + owned["name"]
                or info["Image"] != task["image_id"]
                or labels.get("harnessgrad.admission") != owner
                or labels.get("harnessgrad.task") != task["task_id"]
                or info["State"].get("Status") != "created"
                or info["State"].get("Running") is not False
                or host["NetworkMode"] != "none" or host["ReadonlyRootfs"] is not True
                or host["Privileged"] is not False
                or host.get("CapAdd") or host["CapDrop"] != ["ALL"]
                or host["SecurityOpt"] not in (["no-new-privileges"], ["no-new-privileges=true"], ["no-new-privileges:true"])
                or host.get("PidMode", "") != ""
                or host.get("IpcMode", "private") != "private"
                or host.get("Devices") or host.get("DeviceRequests")
                or host.get("Binds") or host.get("VolumesFrom")
                or config.get("Volumes")
                or host["LogConfig"] != LOG_CONFIG
                or set(info.get("NetworkSettings", {}).get("Networks", {})) - {"none"}):
            raise ValueError("identity, network or security differs")
        for key, expected in dict(Memory=256 << 20, MemorySwap=256 << 20,
                                  PidsLimit=128, NanoCpus=10**9).items():
            if type(host[key]) is not int or host[key] != expected:
                raise ValueError("resource limit differs")
        tmpfs = host["Tmpfs"]
        if not isinstance(tmpfs, dict) or set(tmpfs) != {"/tmp"}:
            raise ValueError("tmpfs mounts differ")
        options = tmpfs["/tmp"].split(",")
        options = ["size=16m" if opt == "size=16777216" else opt for opt in options]
        if len(options) != 7 or set(options) != set(TMPFS.split(",")):
            raise ValueError("tmpfs bounds differ")
        actual = info["Mounts"]
        expected = _mount_plan(mounts)
        # Docker versions may also expose the already checked /tmp tmpfs here.
        binds = []
        for mount in actual:
            if mount.get("Type") == "tmpfs" and mount.get("Destination") == "/tmp" and mount.get("RW") is True:
                continue
            binds.append({key: mount[key] for key in ("Type", "Source", "Destination", "RW")})
            if mount.get("Propagation", "rprivate") != "rprivate":
                raise ValueError("shared bind propagation")
        if binds != expected or any(type(m["RW"]) is not bool for m in binds):
            raise ValueError("artifact mounts differ")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AdmissionError("created container runtime envelope mismatch") from exc


def control_command(task: dict, name: str, owner: str, script: str,
                    mounts: tuple[str, ...] = ()) -> list[str]:
    """A bounded inert shell in a pristine cached image, never an actor snapshot."""
    _task_pin(task)
    _mount_plan(mounts)
    return ["create", "--pull=never", "--name", name, "--network", "none",
            "--label", f"harnessgrad.admission={owner}",
            "--label", f"harnessgrad.task={task['task_id']}",
            "--read-only", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "256m", "--memory-swap", "256m",
            "--cpus", "1", "--log-driver", "local", "--log-opt", "max-size=1m",
            "--log-opt", "max-file=1", "--log-opt", "compress=false", "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m,nr_inodes=4096,mode=1777",
            "--entrypoint", "/bin/sh", "--workdir", "/tmp", *mounts,
            task["image"], "-c", script]


class ControlSession:
    """Write ownership intent before allocation; any uncertain cleanup quarantines."""
    def __init__(self, ledger: ReservationLedger, lease: str, docker: LocalDocker):
        self.ledger, self.lease, self.docker = ledger, lease, docker

    def run(self, task: dict, script: str, *, mounts: tuple[str, ...] = ()) -> dict:
        try:
            return self._run(task, script, mounts=mounts)
        except (AdmissionError, OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
            row = self.ledger.get(self.lease)
            if row["state"] != "released" and row["owned"]:
                self.ledger.update(self.lease, state="quarantined",
                                   cleanup_errors=[f"control uncertainty: {exc}"])
            raise

    def _run(self, task: dict, script: str, *, mounts: tuple[str, ...]) -> dict:
        row = self.ledger.get(self.lease)
        if row["state"] != "active":
            raise AdmissionError("slot is quarantined")
        self.docker.inspect_image(task)  # repeat immediately before every allocation
        name = "hg-admission-" + self.lease + "-" + uuid.uuid4().hex[:12]
        command = control_command(task, name, self.lease, script, mounts)
        owned = row["owned"] + [dict(name=name, id=None, task_id=task["task_id"])]
        self.ledger.update(self.lease, owned=owned)
        proc = self.docker.call(*command)
        if proc.returncode or not re.fullmatch(r"[a-f0-9]{64}", proc.stdout.strip()):
            raise AdmissionError("control create failed; ownership intent retained")
        owned[-1]["id"] = proc.stdout.strip()
        self.ledger.update(self.lease, owned=owned)
        info = self.docker.inspect_container(owned[-1]["id"])
        if info is None:
            raise AdmissionError("created container absence unproven")
        _runtime_envelope(info, task, owned[-1], self.lease, mounts)
        owned[-1]["start_attempted"] = True
        self.ledger.update(self.lease, owned=owned)
        proc = self.docker.call("start", "--attach", owned[-1]["id"], timeout=30)
        info = self.docker.inspect_container(owned[-1]["id"])
        if info is None or info["State"]["Running"]:
            raise AdmissionError("control did not quiesce")
        if proc.returncode or info["State"]["ExitCode"]:
            raise AdmissionError(f"inert control failed: {proc.stderr[-500:]}")
        return dict(task_id=task["task_id"], container_id=owned[-1]["id"],
                    exit_code=info["State"]["ExitCode"], stdout=proc.stdout,
                    runtime=dict(network=info["HostConfig"]["NetworkMode"],
                                 read_only=info["HostConfig"]["ReadonlyRootfs"],
                                 memory=info["HostConfig"]["Memory"],
                                 memory_swap=info["HostConfig"]["MemorySwap"],
                                 pids=info["HostConfig"]["PidsLimit"],
                                 nano_cpus=info["HostConfig"]["NanoCpus"],
                                 tmpfs=info["HostConfig"]["Tmpfs"],
                                 log_config=info["HostConfig"]["LogConfig"]))

    def cleanup(self) -> dict:
        row = self.ledger.get(self.lease)
        if row["state"] == "released":
            return row
        errors = []
        for owned in reversed(row["owned"]):
            try:
                # Names cover create-time timeout; labels must match before removal.
                info = self.docker.inspect_container(owned["id"] or owned["name"])
                if info is None:
                    continue
                labels = info.get("Config", {}).get("Labels", {})
                if (labels.get("harnessgrad.admission") != self.lease
                        or labels.get("harnessgrad.task") != owned["task_id"]):
                    raise AdmissionError("ownership label mismatch; refusing removal")
                if info.get("Name") != "/" + owned["name"]:
                    raise AdmissionError("owned container name changed")
                if (not isinstance(info.get("Id"), str)
                        or not re.fullmatch(r"[a-f0-9]{64}", info["Id"])
                        or (owned["id"] is not None and info["Id"] != owned["id"])):
                    raise AdmissionError("owned container ID changed")
                proc = self.docker.call("rm", "--force", info["Id"])
                if proc.returncode or self.docker.inspect_container(info["Id"]) is not None:
                    raise AdmissionError("removal/absence verification failed")
            except (AdmissionError, OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError) as exc:
                errors.append(f"{owned['name']}: {exc}")
        # Only this label namespace is queried; no unrelated containers are touched.
        try:
            proc = self.docker.call("ps", "-aq", "--filter", f"label=harnessgrad.admission={self.lease}")
            if proc.returncode or proc.stdout.strip():
                errors.append("owned namespace absence unproven")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
        state = "quarantined" if errors else "released"
        return self.ledger.update(self.lease, state=state, cleanup_errors=errors,
                                  cleanup_checked_at=time.time())


def binary_ctrf_verdict(raw: str, report: dict | None, exit_code: int) -> dict:
    """Missing infrastructure is unmeasured; never substitute an exit code for reward."""
    try:
        score = float(raw)
        summary = report["results"]["summary"]
        counts = {key: summary[key] for key in ("tests", "passed", "failed", "skipped", "pending", "other")}
        if (exit_code != 0 or not math.isfinite(score) or score not in (0, 1)
                or any(type(n) is not int or n < 0 for n in counts.values())
                or counts["passed"] + counts["failed"] <= 0
                or counts["tests"] != sum(counts[k] for k in counts if k != "tests")
                or (score == 1) != (counts["failed"] == 0)):
            raise ValueError("invalid reward/report")
        return dict(measured=True, score=score, tests=counts)
    except (ValueError, TypeError, KeyError):
        return dict(measured=False, score=None, reason="missing or invalid scorer infrastructure/evidence")


def trusted_artifact_mount(root: Path, expected_sha256: str) -> tuple[str, ...]:
    """Single data file only. No actor executables, symlinks, directories or toolchain."""
    if root.is_symlink() or not root.is_dir():
        raise AdmissionError("artifact root must be a platform-owned directory")
    entries = list(root.iterdir())
    if len(entries) != 1 or entries[0].name != "answer.txt" or entries[0].is_symlink() or not entries[0].is_file():
        raise AdmissionError("trusted verifier accepts only a sealed answer.txt data file")
    if entries[0].stat().st_size > 4096:
        raise AdmissionError("artifact exceeds boundary limit")
    if hashlib.sha256(entries[0].read_bytes()).hexdigest() != expected_sha256:
        raise AdmissionError("artifact seal mismatch")
    return ("--mount", f"type=bind,src={root.resolve()},dst=/control-input,readonly")
