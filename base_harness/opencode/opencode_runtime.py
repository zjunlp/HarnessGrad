"""Prepared external OpenCode/Bun installation checks, independent of the platform."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

RECORD = "_opencode_runtime"
SCHEMA = 2
REMEDY = "prepare this Harness with tools/configure_opencode_harness.py"
IGNORED = {".git", ".state", "__pycache__", "_harnessgrad", ".opencode-state"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_binding(path: Path, *, forbidden: tuple[Path, ...] = ()) -> None:
    if not path.is_absolute() or ".." in path.parts or not path.exists():
        raise ValueError(f"OpenCode runtime must be an existing absolute path: {path}")
    if not path.is_file() and not path.is_dir():
        raise ValueError(f"OpenCode runtime must be a regular file or directory: {path}")
    temporary = (Path("/tmp"), Path("/var/tmp"), Path(tempfile.gettempdir()))
    for spelling in (path, path.resolve(strict=True)):
        if any(c in str(spelling) for c in (",", "\n", "\r")):
            raise ValueError(f"OpenCode runtime cannot be expressed as a Docker bind mount: {path}")
        if spelling.is_relative_to(Path("/tmp").resolve()) or spelling.is_relative_to("/tmp"):
            raise ValueError(f"OpenCode runtime is under hidden /tmp: {path}")
        for root in forbidden:
            root = root.resolve()
            if spelling.is_relative_to(root) or (path.is_dir() and root.is_relative_to(spelling)):
                raise ValueError(f"OpenCode runtime overlaps the platform or candidate: {path}")
        if not path.is_dir():
            continue
        if any(root.is_relative_to(spelling) or root.resolve().is_relative_to(spelling) for root in temporary):
            raise ValueError(f"OpenCode runtime exposes a shared temporary root: {path}")
        if (any(home.is_relative_to(spelling) for home in (Path.home(), Path.home().resolve()))
                or spelling.parent in (Path("/home"), Path("/Users"))
                or spelling.name in (".config", ".ssh", ".codex", ".opencode")
                or spelling in map(Path, ("/usr", "/usr/local", "/usr/local/bin", "/usr/local/lib",
                                           "/bin", "/sbin", "/lib", "/lib64", "/etc", "/dev", "/proc", "/sys", "/run"))
                or len(spelling.parts) < 3 or (spelling.parts[1:2] == ("mnt",) and len(spelling.parts) <= 3)):
            raise ValueError(f"OpenCode runtime needs a dedicated installation directory: {path}")


def executable(value: str, env: dict) -> Path:
    found = shutil.which(os.path.expanduser(value), path=env.get("PATH", os.defpath))
    if not found:
        raise ValueError(f"Bun executable is missing or not executable: {value}; {REMEDY}")
    return Path(found).resolve(strict=True)


def selection(env: dict, source: str | None = None, bun: str | None = None) -> dict:
    chosen = source if source is not None else env.get("HG_AGENT_OPENCODE_SOURCE")
    if not chosen:
        raise ValueError(f"set --source or HG_AGENT_OPENCODE_SOURCE to a prepared external OpenCode v1 tree; {REMEDY}")
    return {"source": chosen, "bun": bun if bun is not None else
            (env.get("HG_AGENT_OPENCODE_BUN") or env.get("HG_BUN_BINARY") or "bun")}


def _covered(path: Path, bindings: list[Path]) -> bool:
    return any(path == root or (root.is_dir() and path.is_relative_to(root)) for root in bindings)


def inventory(source: Path, bindings: list[Path]) -> dict:
    """Hash every bound installation entry, retaining separate source/metadata summaries."""
    code, packages, links = (hashlib.sha256() for _ in range(3))
    contents = hashlib.sha256()
    counts = {"source_files": 0, "package_manifests": 0, "symlinks": 0, "runtime_files": 0}
    def unreadable(error):
        raise error

    def entry(path: Path, tree: Path) -> None:
        mode = path.lstat().st_mode
        file_sha256 = None
        if stat.S_ISLNK(mode):
            target = path.resolve(strict=True)
            if not _covered(target, bindings):
                raise ValueError(f"OpenCode dependency link needs an explicit --runtime: {path}")
            spelling = os.readlink(path)
            row = [str(path), "link", spelling, str(target)]
            links.update(str(path).encode() + b"\0" + spelling.encode() + b"\0" + str(target).encode() + b"\0")
            counts["symlinks"] += 1
            if target.is_file():
                file_sha256 = digest(target)
                row.append(file_sha256)
        elif stat.S_ISREG(mode):
            file_sha256 = digest(path)
            row = [str(path), "file", stat.S_IMODE(mode), file_sha256]
        elif stat.S_ISDIR(mode):
            row = [str(path), "directory", stat.S_IMODE(mode)]
        else:
            raise ValueError(f"OpenCode installation contains an unsupported filesystem entry: {path}")
        contents.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        if file_sha256 is None:
            return
        counts["runtime_files"] += 1
        if path.name == "package.json":
            packages.update(str(path).encode() + b"\0" + bytes.fromhex(file_sha256))
            counts["package_manifests"] += 1
        relative = path.relative_to(tree)
        if tree == source and not ({"node_modules", *IGNORED} & set(relative.parts)):
            code.update(relative.as_posix().encode() + b"\0" + bytes.fromhex(file_sha256))
            counts["source_files"] += 1

    # Do not exclude cache/VCS-named directories from the runtime digest: they are
    # visible bindings and may contain imported code or data. Third-party and extra
    # dependency bytes must change admission/cache identity even with unchanged stat data.
    for tree in dict.fromkeys((source, *bindings)):
        entry(tree, tree)
        if not tree.is_dir():
            continue
        for directory, dirs, files in os.walk(tree, followlinks=False, onerror=unreadable):
            dirs.sort()
            for name in sorted([*dirs, *files]):
                entry(Path(directory) / name, tree)
    return {"source_sha256": code.hexdigest(), "package_metadata_sha256": packages.hexdigest(),
            "symlinks_sha256": links.hexdigest(), "runtime_contents_sha256": contents.hexdigest(), **counts}


def installation(harness: Path, declaration: dict, env: dict, *, source=None, bun=None,
                 extra: tuple[Path, ...] = (), forbidden: tuple[Path, ...] = ()) -> dict:
    choice = selection(env, source, bun)
    root = Path(choice["source"]).expanduser().resolve(strict=True)
    if not (root / declaration["entrypoint"]).is_file() and (root / declaration["source"]).is_dir():
        root = (root / declaration["source"]).resolve(strict=True)
    binary = executable(choice["bun"], env)
    entry = (root / declaration["entrypoint"]).resolve(strict=True)
    if not entry.is_relative_to(root):
        raise ValueError("OpenCode entrypoint must belong to its source tree")
    bindings = list(dict.fromkeys((root, binary, *(p.expanduser().resolve(strict=True) for p in extra))))
    for path in bindings:
        validate_binding(path, forbidden=(*forbidden, harness))
    package = root / "packages/opencode/package.json"
    if not package.resolve(strict=True).is_relative_to(root):
        raise ValueError("OpenCode package metadata must belong to its source tree")
    metadata = json.loads(package.read_text(encoding="utf-8"))
    version = metadata.get("version", "")
    if metadata.get("name") != "opencode" or not isinstance(version, str) or not re.fullmatch(r"1\.\d+\.\d+(?:[-+][\w.-]+)?", version):
        raise ValueError("this adapter requires a prepared OpenCode v1 source tree")
    required = [binary, entry, package, root / "package.json"]
    required.extend(root / name for name in ("bun.lock", "bun.lockb") if (root / name).is_file())
    required.extend(p for p in bindings if p.is_file())
    files = list(dict.fromkeys(required))
    if not any((root / name).is_file() for name in ("bun.lock", "bun.lockb")):
        raise ValueError("prepared OpenCode source requires its Bun lockfile")
    for path in files:
        if not _covered(path.resolve(strict=True), bindings):
            raise ValueError(f"OpenCode identity file escapes its runtime bindings: {path}")
    return {"schema": SCHEMA, "selection": choice, "source": str(root), "entrypoint": str(entry),
            "binary": str(binary), "source_version": version, "declaration": declaration,
            "runtime_paths": [str(p) for p in bindings], "inventory": inventory(root, bindings),
            "files": [{"path": str(p), "realpath": str(p.resolve(strict=True)), "sha256": digest(p)} for p in files]}


def check(record: dict) -> None:
    try:
        bindings = [Path(p) for p in record["runtime_paths"]]
        for path in bindings:
            validate_binding(path)
            if path.resolve(strict=True) != path:
                raise ValueError(f"prepared OpenCode binding changed: {path}; {REMEDY}")
        for row in record["files"]:
            path = Path(row["path"])
            if not path.is_absolute() or ".." in path.parts or not _covered(path, bindings):
                raise ValueError(f"prepared OpenCode identity file is outside its bindings: {path}; {REMEDY}")
            realpath = path.resolve(strict=True)
            if str(realpath) != row["realpath"] or not _covered(realpath, bindings):
                raise ValueError(f"prepared OpenCode identity file is outside its bindings: {path}; {REMEDY}")
            if digest(path) != row["sha256"]:
                raise ValueError(f"prepared OpenCode runtime file changed: {path}; {REMEDY}")
        if inventory(Path(record["source"]), bindings) != record["inventory"]:
            raise ValueError(f"prepared OpenCode source or dependency identity changed; {REMEDY}")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"prepared OpenCode runtime changed or became unavailable; {REMEDY}") from exc


def _resolve(harness: Path, env: dict, *, forbidden: tuple[Path, ...]) -> dict:
    manifest = json.loads((harness / "harness.json").read_text(encoding="utf-8"))
    record = manifest.get(RECORD)
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise ValueError(f"external OpenCode installation needs an updated Harness and preparation; {REMEDY}")
    paths = ("source", "entrypoint", "binary")
    files = record.get("files")
    if (not all(isinstance(record.get(k), str) and Path(record[k]).is_absolute() for k in paths)
            or not isinstance(record.get("runtime_paths"), list)
            or not all(isinstance(p, str) and Path(p).is_absolute() for p in record["runtime_paths"])
            or not isinstance(files, list) or not files
            or not all(isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ("path", "realpath", "sha256"))
                       and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) for row in files)
            or not isinstance(record.get("inventory"), dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(record["inventory"].get("runtime_contents_sha256", "")))
            or not isinstance(record.get("selection"), dict)
            or not all(isinstance(record["selection"].get(k), str) for k in ("source", "bun"))
            or not isinstance(record.get("bun_version"), str) or not record["bun_version"].strip()
            or not isinstance(record.get("source_version"), str)
            or not re.fullmatch(r"1\.\d+\.\d+(?:[-+][\w.-]+)?", record["source_version"])
            or not isinstance(record.get("declaration"), dict)
            or set(record["declaration"]) != {"kind", "source", "entrypoint", "package"}
            or not isinstance(record["declaration"].get("entrypoint"), str)
            or record.get("declaration") != manifest.get("runtime")):
        raise ValueError(f"invalid prepared OpenCode identity; {REMEDY}")
    source, entry, binary = (Path(record[k]) for k in paths)
    required = {str(binary), str(entry), str(source / "package.json"), str(source / "packages/opencode/package.json")}
    required.update(str(source / name) for name in ("bun.lock", "bun.lockb") if (source / name).is_file())
    required.update(p for p in record["runtime_paths"] if Path(p).is_file())
    pinned = {row["path"] for row in files}
    if (not required <= pinned or len(pinned) != len(files)
            or not {str(source), str(binary)} <= set(record["runtime_paths"])
            or entry != (source / record["declaration"]["entrypoint"]).resolve()
            or not entry.is_relative_to(source) or not any(str(source / p) in pinned for p in ("bun.lock", "bun.lockb"))
            or manifest.get("runtime_paths") != record["runtime_paths"]):
        raise ValueError(f"OpenCode bindings or command differ from the prepared installation; {REMEDY}")
    for path in record["runtime_paths"]:
        validate_binding(Path(path), forbidden=(*forbidden, harness))
    for variable in ("HG_HARNESS_SOURCE", "HG_AGENT_OPENCODE_SOURCE"):
        value = env.get(variable)
        if value and value != record["selection"]["source"] and Path(value).expanduser().resolve() != source:
            raise ValueError(f"OpenCode source override differs from the prepared installation; {REMEDY}")
    named = env.get("HG_HARNESS_BUN") or env.get("HG_AGENT_OPENCODE_BUN") or env.get("HG_BUN_BINARY")
    if named and named != record["selection"]["bun"] and executable(named, env) != binary:
        raise ValueError(f"Bun override differs from the prepared installation; {REMEDY}")
    if env.get("HG_HARNESS_ENTRYPOINT") and Path(env["HG_HARNESS_ENTRYPOINT"]).resolve() != entry:
        raise ValueError(f"OpenCode entrypoint override differs from the prepared installation; {REMEDY}")
    check(record)
    if json.loads((source / "packages/opencode/package.json").read_text()).get("version") != record["source_version"]:
        raise ValueError(f"OpenCode source version differs from the prepared installation; {REMEDY}")
    executable(str(binary), {"PATH": ""})
    return record


def resolve(harness: Path, env: dict, *, forbidden: tuple[Path, ...] = ()) -> dict:
    try:
        return _resolve(harness, env, forbidden=forbidden)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"prepared OpenCode runtime changed or became unavailable; {REMEDY}") from exc
