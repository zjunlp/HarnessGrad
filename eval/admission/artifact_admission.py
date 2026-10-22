"""Offline contracts for trusted scorer bundles and data-only state projections.

These functions verify declared closure integrity, export bounded regular files,
and check inspected runtime limits. They never install, execute or discover scorer
dependencies. Callers own the declaration pin, platform-only destination parent,
quiesced source and runtime observations. Synthetic evidence does not admit H0.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time

from eval.admission.pilot_admission import AdmissionError, LOG_CONFIG

# A distinct unprivileged scorer reads only the two bounded read-only mounts.
SCORER_UID = 65534


def scorer_user() -> str:
    if os.geteuid() == SCORER_UID:
        raise AdmissionError("reserved scorer UID must differ from platform UID")
    return f"{SCORER_UID}:{os.getegid()}"


SCORER_ENV = ["PATH=/control-scorer", "HOME=/tmp", "TMPDIR=/tmp"]


class EvidenceUnavailable(AdmissionError):
    """An incomplete observation or unavailable bytes leave admission unknown."""


def _digest(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value))


def _load_sealed_object(raw: bytes, expected_sha256: str, *, label: str) -> dict:
    """Authenticate exact bytes and reject ambiguous JSON for both declarations."""
    if (not isinstance(raw, bytes) or not _digest(expected_sha256)
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise AdmissionError(f"{label} seal mismatch")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AdmissionError(f"duplicate {label} JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, RecursionError) as exc:
        raise AdmissionError(f"malformed {label} JSON") from exc
    if not isinstance(value, dict):
        raise AdmissionError(f"invalid {label} object")
    return value


def _positive(value) -> bool:
    return type(value) is int and value > 0


def _relative(value) -> str:
    if (not isinstance(value, str) or not value or value.startswith("/")
            or any(c in value for c in "\\\x00\n\r")
            or any(p in ("", ".", "..") for p in value.split("/"))):
        raise AdmissionError("explicit canonical relative file path required")
    return value


def _inodes(paths) -> int:
    # Count every directory, including the projection root, and every file.
    return len(set(paths) | {"."} | {
        str(parent) for path in paths for parent in PurePosixPath(path).parents})


def _paths(rows: list[dict]) -> None:
    names = [_relative(row.get("path")) for row in rows]
    if len(set(names)) != len(names):
        raise AdmissionError("duplicate file path")
    if any(str(parent) in names for path in names for parent in PurePosixPath(path).parents):
        raise AdmissionError("file path collides with a directory")


def _root(path: Path) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise AdmissionError("projection/source path contains a symlink")


def validate_scorer_spec(scorer: dict) -> None:
    """Validate the shared sealed command and declared dependency graph."""
    try:
        if (not isinstance(scorer, dict) or set(scorer) != {"entrypoint", "argv", "files"}
                or not isinstance(scorer["files"], list) or not scorer["files"]):
            raise AdmissionError("explicit scorer closure required")
        roles = set()
        for row in scorer["files"]:
            if (not isinstance(row, dict)
                    or set(row) != {"path", "sha256", "bytes", "role", "requires"}
                    or not _digest(row["sha256"])
                    or type(row["bytes"]) is not int or row["bytes"] < 0
                    or row["role"] not in ("scorer", "runtime", "dependency")
                    or not isinstance(row["requires"], list)
                    or any(not isinstance(p, str) for p in row["requires"])
                    or len(set(row["requires"])) != len(row["requires"])):
                raise AdmissionError("invalid pinned scorer file")
            roles.add(row["role"])
        _paths(scorer["files"])
        nodes = {row["path"]: row for row in scorer["files"]}
        if (not isinstance(scorer["entrypoint"], str)
                or scorer["entrypoint"] not in nodes
                or nodes[scorer["entrypoint"]]["role"] != "scorer"
                or not {"scorer", "runtime"} <= roles
                or any(set(row["requires"]) - nodes.keys() for row in nodes.values())):
            raise AdmissionError("scorer dependency graph is not closed")
        reachable, pending = set(), [scorer["entrypoint"]]
        while pending:
            name = pending.pop()
            if name not in reachable:
                reachable.add(name)
                pending.extend(nodes[name]["requires"])
        if reachable != nodes.keys():
            raise AdmissionError("scorer bundle contains undeclared unused closure nodes")
        argv = scorer["argv"]
        prefix = "/control-scorer/"
        if (not isinstance(argv, list) or len(argv) < 2
                or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
                or not argv[0].startswith(prefix)
                or argv[0][len(prefix):] not in nodes
                or nodes[argv[0][len(prefix):]]["role"] != "runtime"
                or argv[1] != prefix + scorer["entrypoint"]):
            raise AdmissionError("scorer command must use the sealed runtime and entrypoint")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise AdmissionError("malformed scorer declaration") from exc


def load_contract(raw: bytes, expected_sha256: str) -> dict:
    """Accept only a caller-pinned declaration; no truth-valued attestation fields."""
    contract = _load_sealed_object(raw, expected_sha256, label="artifact contract")
    try:
        if (set(contract) != {"schema", "task_id", "source_sha256", "runtime_image_id",
                              "scorer", "export", "quota"}
                or contract["schema"] != "harnessgrad.artifact-scorer.v1"
                or not isinstance(contract["task_id"], str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", contract["task_id"])
                or not _digest(contract["source_sha256"])
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", contract["runtime_image_id"])):
            raise AdmissionError("invalid artifact contract identity")
        validate_scorer_spec(contract["scorer"])
        export = contract["export"]
        if (set(export) != {"files", "max_bytes", "max_inodes"}
                or not export["files"] or not _positive(export["max_bytes"])
                or not _positive(export["max_inodes"])):
            raise AdmissionError("bounded state export required")
        for row in export["files"]:
            if set(row) != {"path", "max_bytes"} or not _positive(row["max_bytes"]):
                raise AdmissionError("invalid state export file bound")
        _paths(export["files"])
        if _inodes([row["path"] for row in export["files"]]) > export["max_inodes"]:
            raise AdmissionError("state export inode quota exceeded")
        quota = contract["quota"]
        # Supported runtime writes are bounded tmpfs scratch/output only. Disk-backed
        # writable actor layers or project quotas require a separate real backend.
        if not quota or set(quota) - {"/tmp", "/control-output"}:
            raise AdmissionError("unsupported writable runtime quota surface")
        for limits in quota.values():
            if (set(limits) != {"bytes", "inodes"}
                    or not all(_positive(v) for v in limits.values())):
                raise AdmissionError("hard byte and inode limits required")
        return contract
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise AdmissionError("malformed artifact contract") from exc


@contextlib.contextmanager
def _regular(root: Path, relative: str, *, data_only: bool = False):
    """Traverse using directory descriptors; never follow a link in any component."""
    relative = _relative(relative)
    _root(root)
    with contextlib.ExitStack() as stack:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        for component in relative.split("/")[:-1]:
            fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        fd = os.open(relative.split("/")[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=fd)
        stream = stack.enter_context(os.fdopen(fd, "rb"))
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (data_only and info.st_mode & 0o111)):
            raise AdmissionError("state/closure must be regular files with one link; state must be data-only")
        yield stream, info
        # Reads can update atime; compare write-relevant fields only.
        after = os.fstat(stream.fileno())
        if any(getattr(after, key) != getattr(info, key) for key in
               ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")):
            raise AdmissionError("source changed during projection")


def _inventory(root: Path) -> set[str]:
    """Used only on platform-owned closure/export trees, never scan actor state."""
    _root(root)
    if not root.exists():
        raise EvidenceUnavailable("platform projection root unavailable")
    if not root.is_dir():
        raise AdmissionError("platform projection root must be a directory")
    found = set()
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            if path.is_symlink():
                raise AdmissionError("projection contains a symlink")
            found.add(path.relative_to(root).as_posix())
    return found


def verify_scorer_files(root: Path, scorer: dict) -> dict:
    """Check exact declared local bytes; do not infer imports or execute anything.

    The caller must authenticate the declaration and keep the tree platform-owned
    and quiesced through use. This check does not establish runtime closure.
    """
    validate_scorer_spec(scorer)
    rows = scorer["files"]
    names = {row["path"] for row in rows}
    expected = names | {str(p) for name in names for p in PurePosixPath(name).parents if str(p) != "."}
    try:
        if _inventory(root) != expected:
            raise AdmissionError("scorer bundle differs from exact closure allowlist")
        for row in rows:
            with _regular(root, row["path"]) as (stream, info):
                if info.st_size != row["bytes"]:
                    raise AdmissionError("scorer file size mismatch")
                digest = hashlib.sha256()
                remaining = row["bytes"]
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise AdmissionError("scorer file truncated")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if stream.read(1) or digest.hexdigest() != row["sha256"]:
                    raise AdmissionError("scorer file seal mismatch")
    except OSError as exc:
        raise EvidenceUnavailable("local scorer closure unavailable") from exc
    return dict(files=len(rows), bytes=sum(row["bytes"] for row in rows),
                scope="local_declared_closure_integrity")


def verify_scorer_closure(root: Path, raw: bytes, expected_sha256: str) -> dict:
    """Verify a task-bound bundle using the same core as reusable scorer manifests.

    Official-source completeness and the pinned image's ABI still need independent
    runtime evidence; hashes cannot prove missing dependencies absent.
    """
    contract = load_contract(raw, expected_sha256)
    counts = verify_scorer_files(root, contract["scorer"])
    return dict(schema="harnessgrad.scorer-closure-receipt.v1", task_id=contract["task_id"],
                contract_sha256=expected_sha256, source_sha256=contract["source_sha256"],
                runtime_image_id=contract["runtime_image_id"], **counts)


def observe_selected_source(source: Path, raw: bytes, expected_sha256: str) -> dict:
    """Bounded endpoint probe, not writer exclusion or continuous quiescence.

    Inspect only allowlisted regular files using the export's no-link traversal.
    Ordinary writes (including write/restore and utime) leave metadata evidence.
    Unselected files, directory history and changes hidden from both endpoint
    probes are outside this observation. The host, observer and filesystem must
    be trusted; a matching result does not prove absence of interval writes.
    """
    contract = load_contract(raw, expected_sha256)
    started = time.monotonic_ns()
    records, total = [], 0
    for row in contract["export"]["files"]:
        with _regular(source, row["path"], data_only=True) as (stream, info):
            limit = min(row["max_bytes"], contract["export"]["max_bytes"] - total)
            if info.st_size > limit:
                raise AdmissionError("source observation byte quota exceeded")
            digest, size = hashlib.sha256(), 0
            while True:
                chunk = stream.read(min(65536, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise AdmissionError("source observation byte quota exceeded")
                digest.update(chunk)
            if size != info.st_size:
                raise AdmissionError("source size changed during observation")
            records.append(dict(path=row["path"], bytes=size, sha256=digest.hexdigest(),
                                **{key: getattr(info, key) for key in (
                                    "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
                                    "st_nlink", "st_mode", "st_uid", "st_gid")}))
            total += size
    return dict(started_monotonic_ns=started, finished_monotonic_ns=time.monotonic_ns(),
                files=records)


def execution_identity(task_id: str, outer_run_id: str, trial_id: str) -> dict:
    """Caller-owned labels, never inferred from actor files or an export receipt."""
    identity = dict(task_id=task_id, outer_run_id=outer_run_id, trial_id=trial_id)
    if any(not isinstance(value, str) or not value.strip()
           or any(ord(c) < 32 or ord(c) == 127 for c in value) for value in identity.values()):
        raise AdmissionError("explicit task/run/trial execution identity required")
    return identity


def _execution_binding(binding: dict, task_id: str) -> dict:
    if not isinstance(binding, dict) or set(binding) != {"task_id", "outer_run_id", "trial_id"}:
        raise AdmissionError("explicit task/run/trial execution identity required")
    identity = execution_identity(**binding)
    if identity["task_id"] != task_id:
        raise AdmissionError("execution identity differs from artifact contract task")
    return identity


def export_state(source: Path, destination: Path, raw: bytes, expected_sha256: str,
                 *, run_id: str, execution_binding: dict | None = None) -> dict:
    """Copy an explicit data allowlist, with byte/inode limits enforced before writes.

    Source must be quiesced by the caller. The exclusive destination and its parent
    must be platform-owned; its files cannot supply a grader toolchain. Unselected
    actor files are never traversed. No old destination is reused or overwritten.
    Metadata checks detect observable source changes; they cannot establish quiescence.
    The platform primary group grants the separate scorer UID read/search access;
    host custody still rests on the exclusive platform-owned parent and RO mounts.
    """
    contract = load_contract(raw, expected_sha256)
    binding = _execution_binding(execution_binding, contract["task_id"]) if execution_binding is not None else None
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", run_id):
        raise AdmissionError("fresh export run identity required")
    export = contract["export"]
    created = False
    try:
        _root(destination)
        source_path, destination_path = source.resolve(), destination.resolve()
        if (source_path == destination_path or source_path in destination_path.parents
                or destination_path in source_path.parents):
            raise AdmissionError("state source and platform projection must be separate")
        destination.mkdir(mode=0o700)
        created = True
        os.chown(destination, -1, os.getegid())
        destination.chmod(0o750)
        total, records = 0, []
        for row in export["files"]:
            with _regular(source, row["path"], data_only=True) as (stream, info):
                limit = min(row["max_bytes"], export["max_bytes"] - total)
                if info.st_size > limit:
                    raise AdmissionError("state export byte quota exceeded")
                target = destination / row["path"]
                # Create each directory explicitly: parents=True does not apply
                # the requested mode to intermediate parents, and umask may mask it.
                parent = destination
                for part in PurePosixPath(row["path"]).parts[:-1]:
                    parent = parent / part
                    if not parent.exists():
                        parent.mkdir(mode=0o700)
                        os.chown(parent, -1, os.getegid())
                        parent.chmod(0o750)
                digest, size = hashlib.sha256(), 0
                with target.open("xb") as output:
                    os.chown(target, -1, os.getegid())
                    os.chmod(target, 0o640)
                    while True:
                        chunk = stream.read(min(65536, limit - size + 1))
                        if not chunk:
                            break
                        if size + len(chunk) > limit:
                            raise AdmissionError("state export byte quota exceeded")
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                if size != info.st_size:
                    raise AdmissionError("source size changed during projection")
                total += size
                records.append(dict(path=row["path"], bytes=size, sha256=digest.hexdigest()))
        return dict(schema="harnessgrad.state-export-receipt.v2" if binding is not None
                    else "harnessgrad.state-export-receipt.v1", run_id=run_id,
                    task_id=contract["task_id"], contract_sha256=expected_sha256,
                    source_sha256=contract["source_sha256"], files=records,
                    bytes=total, inodes=_inodes([row["path"] for row in records]),
                    limits={key: export[key] for key in ("max_bytes", "max_inodes")},
                    scope="local_artifact_projection",
                    **({"execution_binding": binding} if binding is not None else {}))
    except (OSError, AdmissionError) as exc:
        if created:
            try:
                shutil.rmtree(destination)
            except OSError as cleanup:
                raise EvidenceUnavailable("failed state export cleanup; partial projection retained") from cleanup
        if isinstance(exc, AdmissionError):
            raise
        raise EvidenceUnavailable("state export unavailable; existing destination is never reused") from exc


def verify_state_export(root: Path, receipt: dict, raw: bytes, expected_sha256: str,
                        *, run_id: str, execution_binding: dict | None = None) -> None:
    """Recheck a platform export receipt immediately before a read-only mount."""
    contract = load_contract(raw, expected_sha256)
    binding = _execution_binding(execution_binding, contract["task_id"]) if execution_binding is not None else None
    try:
        export = contract["export"]
        schema = "harnessgrad.state-export-receipt.v2" if binding is not None else "harnessgrad.state-export-receipt.v1"
        if (receipt["schema"] != schema or receipt.get("execution_binding") != binding
                or receipt["scope"] != "local_artifact_projection"
                or receipt["run_id"] != run_id or receipt["task_id"] != contract["task_id"]
                or receipt["contract_sha256"] != expected_sha256
                or receipt["source_sha256"] != contract["source_sha256"]
                or receipt["limits"] != {key: export[key] for key in ("max_bytes", "max_inodes")}
                or [row["path"] for row in receipt["files"]] != [row["path"] for row in export["files"]]):
            raise AdmissionError("state export receipt identity mismatch")
        names = {row["path"] for row in export["files"]}
        expected = names | {str(p) for name in names for p in PurePosixPath(name).parents if str(p) != "."}
        if _inventory(root) != expected:
            raise AdmissionError("state projection differs from data allowlist")
        total = 0
        for row, bound in zip(receipt["files"], export["files"]):
            if (type(row["bytes"]) is not int or not 0 <= row["bytes"] <= bound["max_bytes"]
                    or not _digest(row["sha256"])):
                raise AdmissionError("invalid state export seal")
            total += row["bytes"]
            if total > export["max_bytes"]:
                raise AdmissionError("state export byte quota exceeded")
            with _regular(root, row["path"], data_only=True) as (stream, info):
                if info.st_size != row["bytes"]:
                    raise AdmissionError("state projection size mismatch")
                digest, remaining = hashlib.sha256(), info.st_size
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise AdmissionError("state projection truncated")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if stream.read(1) or digest.hexdigest() != row["sha256"]:
                    raise AdmissionError("state projection seal mismatch")
        if receipt["bytes"] != total or receipt["inodes"] != _inodes(names):
            raise AdmissionError("state export accounting mismatch")
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        raise EvidenceUnavailable("state export evidence unavailable") from exc


def runtime_tmpfs(raw: bytes, expected_sha256: str) -> dict[str, str]:
    """Build hard tmpfs limit options for an adapter to apply and inspect."""
    contract = load_contract(raw, expected_sha256)
    return _tmpfs_options(contract)


def _tmpfs_options(contract: dict) -> dict[str, str]:
    return {path: f"rw,nosuid,nodev,noexec,size={limits['bytes']},nr_inodes={limits['inodes']},mode=1777"
            for path, limits in contract["quota"].items()}


def require_artifact_runtime(info: dict, raw: bytes, expected_sha256: str,
                             *, artifact_root: Path, closure_root: Path,
                             export_receipt: dict, run_id: str,
                             execution_binding: dict | None = None) -> dict:
    """Validate a stopped pristine runtime observation, without creating/executing it.

    Only the two platform projections may be bound read-only; every writable mount
    must be one of the bounded tmpfs regions. The existing control runtime validates
    ownership, CPU/memory/PID limits and cleanup separately. This is not a launch gate.
    """
    contract = load_contract(raw, expected_sha256)
    verify_scorer_files(closure_root, contract["scorer"])
    verify_state_export(artifact_root, export_receipt, raw, expected_sha256, run_id=run_id,
                        execution_binding=execution_binding)
    receipt = _verify_artifact_runtime(info, contract, expected_sha256,
                                       artifact_root=artifact_root, closure_root=closure_root)
    if execution_binding is not None:
        receipt["execution_binding"] = _execution_binding(execution_binding, contract["task_id"])
    return receipt


def _require_scorer_access(root: Path, rows: list[dict], *, executable: str | None = None) -> None:
    """Check DAC access inside each bind root, not private host-side ancestors.

    The scorer uses one explicit UID/GID, without supplemental groups or DAC caps.
    POSIX owner/group/other precedence matters even when another class has access.
    """
    scorer_user()  # Fail closed if the reserved identity is the platform writer.
    files = {row["path"] for row in rows}
    directories = {"."} | {str(p) for name in files for p in PurePosixPath(name).parents}
    for name in files | directories:
        info = (root / name).stat()
        shift = 6 if info.st_uid == SCORER_UID else 3 if info.st_gid == os.getegid() else 0
        required = 5 if name in directories else 5 if name == executable else 4
        if ((stat.S_IMODE(info.st_mode) >> shift) & required) != required:
            raise AdmissionError("platform projection is not readable/searchable by scorer identity")


def _verify_artifact_runtime(info: dict, contract: dict, expected_sha256: str,
                             *, artifact_root: Path, closure_root: Path) -> dict:
    """Shared observation check; callers must first authenticate and verify both trees."""
    try:
        _require_scorer_access(artifact_root, contract["export"]["files"])
        _require_scorer_access(closure_root, contract["scorer"]["files"],
                               executable=contract["scorer"]["argv"][0][len("/control-scorer/"):])
        host, config = info["HostConfig"], info["Config"]
        if (info["Image"] != contract["runtime_image_id"]
                or config["Entrypoint"] != contract["scorer"]["argv"][:1]
                or config["Cmd"] != contract["scorer"]["argv"][1:]
                or config["Env"] != SCORER_ENV or config["WorkingDir"] != "/tmp"
                or config["User"] != scorer_user() or host.get("GroupAdd")
                or info["State"]["Status"] != "created" or info["State"]["Running"] is not False
                or host["NetworkMode"] != "none" or host["ReadonlyRootfs"] is not True
                or host["Privileged"] is not False or host["CapDrop"] != ["ALL"]
                or host.get("CapAdd") or host.get("Devices") or host.get("DeviceRequests")
                or host.get("Binds") or host.get("VolumesFrom") or config.get("Volumes")
                or host.get("PidMode", "") != "" or host.get("IpcMode", "private") != "private"
                or host["SecurityOpt"] not in (["no-new-privileges"], ["no-new-privileges=true"],
                                              ["no-new-privileges:true"])
                or host["LogConfig"] != LOG_CONFIG
                or set(info.get("NetworkSettings", {}).get("Networks", {})) - {"none"}):
            raise AdmissionError("trusted artifact runtime boundary mismatch")
        expected = _tmpfs_options(contract)
        if set(host["Tmpfs"]) != set(expected):
            raise AdmissionError("writable runtime surface differs from quota contract")
        for path, options in expected.items():
            observed = host["Tmpfs"][path].split(",")
            if len(observed) != 7 or set(observed) != set(options.split(",")):
                raise AdmissionError("runtime hard byte/inode quota mismatch")
        mounts = []
        for mount in info["Mounts"]:
            if mount["Type"] == "tmpfs":
                if mount["Destination"] not in expected or mount["RW"] is not True:
                    raise AdmissionError("unbounded writable runtime mount")
                continue
            if mount.get("Propagation", "rprivate") != "rprivate":
                raise AdmissionError("shared artifact bind propagation")
            mounts.append({key: mount[key] for key in ("Type", "Source", "Destination", "RW")})
        expected_binds = [dict(Type="bind", Source=str(root.resolve(strict=True)),
                               Destination=target, RW=False) for root, target in
                          ((artifact_root, "/control-input"), (closure_root, "/control-scorer"))]
        if (any(root.is_symlink() or not root.is_dir() for root in (artifact_root, closure_root))
                or sorted(mounts, key=lambda m: m["Destination"]) != expected_binds
                or any(m["RW"] is not False for m in mounts)):
            raise AdmissionError("artifact-only read-only mounts required")
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        raise EvidenceUnavailable("incomplete artifact runtime observation") from exc
    return dict(schema="harnessgrad.artifact-runtime-receipt.v1", contract_sha256=expected_sha256,
                task_id=contract["task_id"], runtime_image_id=contract["runtime_image_id"],
                tmpfs=expected, scorer_user=scorer_user(), scope="inspected_runtime_contract")
