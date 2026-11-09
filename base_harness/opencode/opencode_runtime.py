"""Prepared OpenCode release checks, independent of the platform."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

RECORD = "_opencode_runtime"
SCHEMA = 3
REMEDY = "prepare this Harness with tools/configure_opencode_harness.py"
PACKAGES = {"1": "opencode-linux-x64", "2": "@opencode/cli-linux-x64"}


def execution_wire(env: dict) -> str:
    wire = env.get("HG_AGENT_OPENCODE_WIRE", "chat")
    if wire not in ("chat", "responses"):
        raise ValueError("HG_AGENT_OPENCODE_WIRE must be chat or responses")
    return wire


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


def _linux_x64_elf(binary: Path) -> bool:
    """Check the native package format before running the sandbox version probe."""
    with binary.open("rb") as stream:
        header = stream.read(64)
        file_size = os.fstat(stream.fileno()).st_size
    if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
        return False
    kind, machine, version = struct.unpack_from("<HHI", header, 16)
    program_offset = struct.unpack_from("<Q", header, 32)[0]
    header_size, entry_size, count = struct.unpack_from("<HHH", header, 52)
    return (kind in (2, 3) and machine == 62 and version == 1
            and header_size == 64 and entry_size == 56 and count > 0
            and program_offset >= header_size and program_offset + count * entry_size <= file_size)


def installation(binary: Path, *, forbidden: tuple[Path, ...] = ()) -> dict:
    binary = binary.resolve(strict=True)
    validate_binding(binary, forbidden=forbidden)
    metadata = binary.parent.parent / "package.json"
    validate_binding(metadata, forbidden=forbidden)
    if (binary.name != "opencode" or binary.parent.name != "bin"
            or not binary.is_file() or not os.access(binary, os.X_OK)
            or metadata.resolve(strict=True) != metadata or not metadata.is_file()):
        raise ValueError(f"expected an executable from an OpenCode npm native package; {REMEDY}")
    if not _linux_x64_elf(binary):
        raise ValueError(f"OpenCode npm executable must be a Linux x86-64 ELF binary; {REMEDY}")
    package = json.loads(metadata.read_text(encoding="utf-8"))
    version = package.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[12]\.[0-9]+\.[0-9]+", version):
        raise ValueError("OpenCode native package must declare an exact v1 or v2 release")
    profile = version.split(".", 1)[0]
    if package.get("name") != PACKAGES[profile]:
        raise ValueError("OpenCode native package name does not match its release series")
    return {"schema": SCHEMA, "argv": [str(binary)], "version": version, "profile": profile,
            "runtime_paths": [str(binary), str(metadata)],
            "sha256": [digest(binary), digest(metadata)],
            "modes": [stat.S_IMODE(path.stat().st_mode) for path in (binary, metadata)],
            "cacheable": True}


def resolve(harness: Path, _env: dict, *, forbidden: tuple[Path, ...] = ()) -> dict:
    manifest = json.loads((harness / "harness.json").read_text(encoding="utf-8"))
    record = manifest.get(RECORD)
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise ValueError(f"OpenCode release is unprepared; {REMEDY}")
    argv, paths = record.get("argv"), record.get("runtime_paths")
    if (not isinstance(argv, list) or len(argv) != 1 or not isinstance(argv[0], str)
            or not isinstance(paths, list) or manifest.get("runtime_paths") != paths):
        raise ValueError(f"invalid OpenCode runtime binding; {REMEDY}")
    binary = Path(argv[0])
    if (not binary.is_absolute() or binary.resolve() != binary
            or paths != [str(binary), str(binary.parent.parent / "package.json")]):
        raise ValueError(f"invalid OpenCode runtime paths; {REMEDY}")
    if installation(binary, forbidden=(*forbidden, harness)) != record:
        raise ValueError(f"OpenCode installation changed; {REMEDY}")
    return record
