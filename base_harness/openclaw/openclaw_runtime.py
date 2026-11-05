"""Candidate-owned OpenClaw installation checks; no platform imports or discovery at task time."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


RECORD = "_openclaw_runtime"
REMEDY = "prepare this Harness with tools/configure_openclaw_harness.py"
NODE_MINIMUM = (22, 12, 0)
SYSTEM = tuple(map(Path, ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc",
                          "/dev", "/proc", "/sys", "/run")))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(harness: Path) -> dict:
    expected = json.loads((harness / "runtime-identity.json").read_text(encoding="utf-8"))
    if (not isinstance(expected, dict) or expected.get("source") != "openclaw"
            or not isinstance(expected.get("version"), str)
            or not re.fullmatch(r"\d{4}\.\d+\.\d+", expected["version"])
            or not isinstance(expected.get("upstream_commit"), str)
            or any(name in expected for name in ("entrypoint", "package_root", "node"))):
        raise ValueError(f"invalid portable OpenClaw identity; {REMEDY}")
    return expected


def executable(value: str, env: dict) -> Path:
    found = shutil.which(os.path.expanduser(value), path=env.get("PATH", os.defpath))
    if not found:
        raise ValueError(f"OpenClaw executable is missing or not executable: {value}; {REMEDY}")
    return Path(found).resolve(strict=True)


def choice(env: dict, *, openclaw: str | None = None, node: str | None = None) -> dict:
    return {"openclaw": openclaw if openclaw is not None else (env.get("HG_AGENT_OPENCLAW") or "openclaw"),
            "node": node if node is not None else (env.get("HG_OPENCLAW_NODE") or "node")}


def validate_binding(path: Path, *, forbidden: tuple[Path, ...] = ()) -> None:
    """Admit files or dedicated installation trees without exposing shared roots."""
    if not path.is_absolute() or ".." in path.parts or not path.exists():
        raise ValueError(f"OpenClaw runtime must be an existing absolute path: {path}")
    directory = path.is_dir()
    if not directory and not path.is_file():
        raise ValueError(f"OpenClaw runtime must be a regular file or directory: {path}")
    temporary = (Path("/tmp"), Path("/var/tmp"), Path(tempfile.gettempdir()))
    for spelling in (path, path.resolve(strict=True)):
        if any(c in str(spelling) for c in (",", "\n", "\r")):
            raise ValueError(f"OpenClaw runtime cannot be expressed as a Docker bind mount: {path}")
        if any(spelling.is_relative_to(root) for root in (Path("/tmp"), Path("/tmp").resolve())):
            raise ValueError(f"OpenClaw runtime is under hidden /tmp: {path}; use an installation outside /tmp")
        for root in forbidden:
            root = root.resolve()
            if spelling.is_relative_to(root) or (directory and root.is_relative_to(spelling)):
                raise ValueError(f"OpenClaw runtime overlaps the platform or candidate: {path}")
        if not directory:
            continue
        if any(root.is_relative_to(spelling) or root.resolve().is_relative_to(spelling) for root in temporary):
            raise ValueError(f"OpenClaw runtime exposes a shared temporary root: {path}")
        home = Path.home()
        if (home.is_relative_to(spelling) or spelling.parent in (Path("/home"), Path("/Users"))
                or spelling.name in (".openclaw", ".clawdbot", ".codex", ".ssh", ".config")
                or spelling in (*SYSTEM, Path("/usr/local"), Path("/usr/local/bin"), Path("/usr/local/lib"))
                or len(spelling.parts) < 3
                or (spelling.parts[1:2] == ("mnt",) and len(spelling.parts) <= 3)):
            raise ValueError(f"OpenClaw runtime needs a dedicated installation directory: {path}; bind individual files instead")


def _package(entry: Path) -> Path:
    for root in entry.parents:
        path = root / "package.json"
        if path.is_file():
            try:
                metadata = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(metadata, dict) and metadata.get("name") == "openclaw":
                    return root
            except ValueError:
                pass
    raise ValueError(f"selected launcher does not belong to an installed OpenClaw package: {entry}")


def _covered(path: Path, bindings: list[Path]) -> bool:
    return any(path == tree or (tree.is_dir() and path.is_relative_to(tree)) for tree in bindings)


def _symlinks(bindings: list[Path]) -> list[dict]:
    links = {}
    def unreadable(error):
        raise error
    for tree in bindings:
        if not tree.is_dir():
            continue
        for directory, dirs, names in os.walk(tree, followlinks=False, onerror=unreadable):
            for name in (*dirs, *names):
                path = Path(directory) / name
                if path.is_symlink():
                    links[str(path)] = str(path.resolve(strict=True))
    return [{"path": path, "realpath": target} for path, target in sorted(links.items())]


def _npm_packages(package: Path, *, forbidden: tuple[Path, ...] = (),
                  bindings: list[Path] | None = None) -> tuple[list[Path], list[dict]]:
    """Resolve installed production dependencies without executing package code."""
    pending, seen, lookups = [package], set(), {}
    while pending:
        root = pending.pop().resolve(strict=True)
        if root in seen:
            continue
        validate_binding(root, forbidden=forbidden)
        if bindings is not None and not _covered(root, bindings):
            raise ValueError(f"OpenClaw npm dependency is outside its runtime bindings: {root}; {REMEDY}")
        metadata = root / "package.json"
        if not metadata.is_file() or not metadata.resolve(strict=True).is_relative_to(root):
            raise ValueError(f"npm package needs its own package.json: {root}")
        data = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k] for k in ("name", "version")):
            raise ValueError(f"invalid npm package metadata: {metadata}")
        declarations = []
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            values = data.get(field) or {}
            if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                raise ValueError(f"invalid npm {field}: {metadata}")
            declarations.append(values)
        required, optional, peers = declarations
        seen.add(root)
        for name in sorted(required.keys() | optional.keys() | peers.keys()):
            if (not re.fullmatch(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+", name)
                    or any(part in (".", "..") for part in name.split("/"))):
                raise ValueError(f"invalid npm dependency name {name!r}: {metadata}")
            found = None
            for parent in (root, *root.parents):
                if parent.name == "node_modules":
                    continue
                candidate = parent / "node_modules" / name
                try:
                    candidate.lstat()
                except FileNotFoundError:
                    continue
                if not candidate.is_dir():
                    raise ValueError(f"installed npm dependency is not a directory: {candidate}")
                found = candidate
                break
            if found is None:
                # npm optional declarations override dependencies of the same name.
                # Only installed peers belong to this installation's runtime.
                if name in required and name not in optional:
                    raise ValueError(f"missing installed npm dependency {name!r} required by {root}; install its dependencies before preparation")
                continue
            real = found.resolve(strict=True)
            lookups[str(found)] = str(real)
            pending.append(real)
    return sorted(seen), [{"path": path, "realpath": target} for path, target in sorted(lookups.items())]


def installation(env: dict, *, openclaw: str | None = None, node: str | None = None,
                 extra: tuple[Path, ...] = (), forbidden: tuple[Path, ...] = ()) -> dict:
    selection = choice(env, openclaw=openclaw, node=node)
    launcher = executable(selection["openclaw"], env)
    program = executable(selection["node"], env)
    package = _package(launcher)
    entry = package / "openclaw.mjs"
    metadata = package / "package.json"
    if (not entry.is_file() or entry.resolve() != launcher
            or not launcher.is_relative_to(package)
            or not metadata.resolve().is_relative_to(package)):
        raise ValueError("OpenClaw launcher and package metadata must belong to the selected package")
    bindings: list[Path] = []
    files = [entry, metadata]

    def bind(path: Path):
        for spelling in (path.absolute(), path.resolve(strict=True)):
            validate_binding(spelling, forbidden=forbidden)
            if spelling not in bindings:
                bindings.append(spelling)
            if spelling.is_file() and spelling not in files:
                files.append(spelling)

    # A Node executable does not require exposing its bin directory or NVM prefix.
    bind(program)
    bind(package)
    for path in extra:
        # Aliases select a physical dependency once. Mounting an alias at its own
        # spelling turns it into a regular bind target in the task namespace.
        bind(path.expanduser().resolve(strict=True))
    packages, lookups = _npm_packages(package, forbidden=forbidden)
    for root in packages:
        if not _covered(root, bindings):
            bind(root)
        metadata = root / "package.json"
        if metadata not in files:
            files.append(metadata)
    for lookup in lookups:
        if not _covered(Path(lookup["path"]), bindings):
            raise ValueError(f"npm dependency lookup needs an explicit --runtime directory containing its links: {lookup['path']}")
    # Undeclared links need explicit bindings rather than a mount of their common
    # parent (which can contain configuration or platform files).
    links = _symlinks(bindings)
    for link in links:
        if not _covered(Path(link["realpath"]), bindings):
            raise ValueError(f"OpenClaw runtime symlink needs an explicit --runtime dependency: {link['path']}")
    return {"selection": selection, "node": str(program), "entrypoint": str(entry),
            "package_root": str(package), "argv": [str(program), str(entry)],
            "runtime_paths": [str(p) for p in bindings],
            "binding_identities": [{"path": str(p), "realpath": str(p.resolve(strict=True)),
                                    "kind": "directory" if p.is_dir() else "file"} for p in bindings],
            "packages": [str(p) for p in packages], "package_lookups": lookups,
            "symlinks": links,
            "files": [{"path": str(p), "realpath": str(p.resolve(strict=True)), "sha256": digest(p)}
                      for p in files]}


def node_version(output: str) -> str:
    value = output.strip()
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", value)
    if not match or tuple(map(int, match.groups())) < NODE_MINIMUM:
        raise ValueError("OpenClaw requires Node >=22.12.0")
    return value


def cli_version(output: str, expected: str) -> str:
    versions = re.findall(r"(?<![\w.])(\d{4}\.\d+\.\d+(?:[-+][\w.-]+)?)(?![\w.])", output)
    if versions != [expected]:
        raise ValueError(f"OpenClaw version mismatch: expected {expected}, found {output.strip()!r}")
    return output.strip()


def check_files(record: dict, *, forbidden: tuple[Path, ...] = ()) -> None:
    try:
        for entry in record["binding_identities"]:
            path = Path(entry["path"])
            kind = "directory" if path.is_dir() else "file" if path.is_file() else None
            if str(path.resolve(strict=True)) != entry["realpath"] or kind != entry["kind"]:
                raise ValueError(f"prepared OpenClaw runtime binding changed: {path}; {REMEDY}")
        if _symlinks([Path(p) for p in record["runtime_paths"]]) != record["symlinks"]:
            raise ValueError(f"prepared OpenClaw runtime symlinks changed; {REMEDY}")
        for entry in record["files"]:
            path = Path(entry["path"])
            if str(path.resolve(strict=True)) != entry["realpath"] or digest(path) != entry["sha256"]:
                raise ValueError(f"prepared OpenClaw runtime file changed: {path}; {REMEDY}")
        packages, lookups = _npm_packages(Path(record["package_root"]), forbidden=forbidden,
                                          bindings=[Path(p) for p in record["runtime_paths"]])
        if [str(p) for p in packages] != record["packages"] or lookups != record["package_lookups"]:
            raise ValueError(f"prepared OpenClaw npm dependency graph changed; {REMEDY}")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"prepared OpenClaw runtime changed or became unavailable; {REMEDY}") from exc


def _resolve(harness: Path, env: dict, *, forbidden: tuple[Path, ...]) -> dict:
    manifest = json.loads((harness / "harness.json").read_text(encoding="utf-8"))
    record = manifest.get(RECORD)
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ValueError(f"OpenClaw Harness is not prepared; {REMEDY}")
    expected = identity(harness)
    if record.get("source_identity") != expected or record.get("version") != expected["version"]:
        raise ValueError(f"OpenClaw prepared source identity changed; {REMEDY}")
    names = ("node", "entrypoint", "package_root")
    files, bindings = record.get("files"), record.get("runtime_paths")
    identities, links = record.get("binding_identities"), record.get("symlinks")
    packages, lookups = record.get("packages"), record.get("package_lookups")
    if (not all(isinstance(record.get(k), str) and Path(record[k]).is_absolute() for k in names)
            or not isinstance(files, list) or not files or not isinstance(bindings, list) or not bindings
            or not all(isinstance(p, str) for p in bindings)
            or not all(isinstance(row, dict) and isinstance(row.get("path"), str)
                       and isinstance(row.get("realpath"), str)
                       and isinstance(row.get("sha256"), str)
                       and re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) for row in files)
            or not isinstance(record.get("selection"), dict)
            or not all(isinstance(record["selection"].get(k), str) for k in ("openclaw", "node"))
            or not isinstance(record.get("node_version"), str)
            or not isinstance(record.get("version_output"), str)
            or not isinstance(identities, list) or not isinstance(links, list)
            or not all(isinstance(row, dict) and isinstance(row.get("path"), str)
                       and isinstance(row.get("realpath"), str) for row in [*identities, *links])
            or not all(row.get("kind") in ("file", "directory") for row in identities)
            or not isinstance(packages, list) or not packages
            or not all(isinstance(p, str) and Path(p).is_absolute() for p in packages)
            or not isinstance(lookups, list)
            or not all(isinstance(row, dict) and isinstance(row.get("path"), str)
                       and isinstance(row.get("realpath"), str) for row in lookups)):
        raise ValueError(f"invalid prepared OpenClaw identity; {REMEDY}")
    node, entry, package = (Path(record[k]) for k in names)
    required = {str(node), str(entry), str(package / "package.json")}
    required.update(str(Path(root) / "package.json") for root in packages)
    pinned = {row["path"] for row in files}
    if (len(pinned) != len(files) or not required <= pinned
            or [row["path"] for row in identities] != bindings or len(set(bindings)) != len(bindings)
            or not {str(node), str(package)} <= set(bindings)
            or not {row["path"] for row in identities if row["kind"] == "file"} <= pinned
            or len({row["path"] for row in links}) != len(links)
            or len(set(packages)) != len(packages) or str(package) not in packages
            or record.get("argv") != [str(node), str(entry)]
            or entry != package / "openclaw.mjs"
            or node.resolve() != node or package.resolve() != package
            or not entry.resolve().is_relative_to(package)
            or not (package / "package.json").resolve().is_relative_to(package)):
        raise ValueError(f"invalid prepared OpenClaw command or file identity; {REMEDY}")
    if manifest.get("runtime_paths") != bindings:
        raise ValueError(f"OpenClaw runtime bindings differ from the prepared installation; {REMEDY}")
    trees = [Path(p) for p in bindings]
    for tree in trees:
        validate_binding(tree, forbidden=(*forbidden, harness))
    for row in files:
        path = Path(row["path"])
        if not path.is_absolute() or not _covered(path, trees) or not _covered(path.resolve(), trees):
            raise ValueError(f"OpenClaw identity file is outside its runtime bindings: {path}; {REMEDY}")
    for row in links:
        if not _covered(Path(row["path"]), trees) or not _covered(Path(row["realpath"]), trees):
            raise ValueError(f"OpenClaw runtime symlink is outside its runtime bindings; {REMEDY}")
    if (not all(_covered(Path(root), trees) for root in packages)
            or not all(_covered(Path(row["path"]), trees) and row["realpath"] in packages for row in lookups)):
        raise ValueError(f"OpenClaw npm dependency is outside its runtime bindings; {REMEDY}")
    for variable, name, prepared in (("HG_AGENT_OPENCLAW", "openclaw", entry.resolve()),
                                     ("HG_OPENCLAW_NODE", "node", node)):
        override = env.get(variable)
        if override and override != record["selection"][name] and executable(override, env) != prepared:
            raise ValueError(f"OpenClaw {variable} override differs from the prepared installation; {REMEDY}")
    node_version(record["node_version"])
    cli_version(record["version_output"], expected["version"])
    check_files(record, forbidden=(*forbidden, harness))
    executable(str(node), {"PATH": ""})
    return record


def resolve(harness: Path, env: dict, *, forbidden: tuple[Path, ...] = ()) -> dict:
    try:
        return _resolve(harness, env, forbidden=forbidden)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"prepared OpenClaw runtime changed or became unavailable; {REMEDY}") from exc
