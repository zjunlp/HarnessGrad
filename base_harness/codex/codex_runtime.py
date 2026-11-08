"""Candidate-owned Codex selection and prepared-installation checks (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import struct
import tempfile


RECORD = "_codex_runtime"
REMEDY = "prepare this Harness with tools/configure_codex_harness.py"
SCOPE = "declared-native-or-npm-content; host OS libraries and shell tools are platform capabilities"
SYSTEM = tuple(map(Path, ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc",
                          "/dev", "/proc", "/sys", "/run")))


def require_clean_startup(env: dict) -> None:
    for name in ('NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH'):
        if env.get(name):
            raise ValueError(f'prepared runtime forbids ambient startup variable {name}; {REMEDY}')


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def executable(value: str, env: dict) -> Path:
    found = shutil.which(os.path.expanduser(value), path=env.get("PATH", os.defpath))
    if not found:
        raise ValueError(f"Codex executable is missing or not executable: {value}; {REMEDY}")
    # Python's venv/bin/python spelling selects pyvenv.cfg and site-packages.
    return Path(os.path.abspath(found))


def _shebang(path: Path) -> list[str]:
    with path.open("rb") as stream:
        first = stream.readline(4096)
    if not first.startswith(b"#!"):
        return []
    words = shlex.split(first[2:].decode("utf-8").strip())
    if words and Path(words[0]).name == "env":
        words = words[1:]
        if words[:1] == ["-S"]:
            words = words[1:]
    return words


def _argument_file(value: str) -> Path | None:
    if value.startswith("-"):
        return None
    try:
        path = Path(os.path.expanduser(value))
        return path if path.is_file() else None
    except (OSError, ValueError):
        # Opaque argv can contain inline programs or JSON longer than NAME_MAX.
        return None


def choice(env: dict, *, codex: str | None = None, command: str | None = None) -> list[str]:
    """The operator's argv before PATH discovery or interpreter expansion."""
    if command is not None:
        argv = shlex.split(command)
    elif codex is not None:
        argv = [codex]
    elif env.get("HG_AGENT_CODEX_COMMAND", "").strip():
        argv = shlex.split(env["HG_AGENT_CODEX_COMMAND"])
    else:
        argv = [env.get("HG_AGENT_CODEX") or "codex"]
    if not argv:
        raise ValueError("Codex command must not be empty")
    return argv


def select(env: dict, *, codex: str | None = None, command: str | None = None,
           node: str | None = None) -> list[str]:
    """Resolve an explicit choice, or discover PATH once during preparation."""
    argv = choice(env, codex=codex, command=command)
    program = executable(argv[0], env)
    shebang = _shebang(program)
    if shebang and Path(shebang[0]).name.startswith("node"):
        # Make the npm wrapper's interpreter independent of the task's PATH.
        argv = [str(executable(node or shebang[0], env).resolve()),
                *shebang[1:], str(program.resolve()), *argv[1:]]
    else:
        # Preserve executable symlinks for venv/conda interpreters. Native Codex
        # aliases can use their real path without binding an alias in a user HOME.
        prefix = program.parent.parent
        python_env = (prefix / "pyvenv.cfg").is_file() or (prefix / "conda-meta").is_dir()
        argv[0] = str(program if python_env else program.resolve())
    for index, value in enumerate(argv[1:], 1):
        if (path := _argument_file(value)) is not None:
            argv[index] = os.path.abspath(path)
    return argv


def _system(path: Path) -> bool:
    return any(path == tree or path.is_relative_to(tree) for tree in SYSTEM)


def validate_binding(path: Path, *, forbidden: tuple[Path, ...] = ()) -> None:
    """Permit files or dedicated directories, never reopen hidden/shared trees."""
    if not path.is_absolute() or ".." in path.parts or not path.exists():
        raise ValueError(f"Codex runtime must be an existing absolute path: {path}")
    directory = path.is_dir()
    if not directory and not path.is_file():
        raise ValueError(f"Codex runtime must be a regular file or directory: {path}")
    roots = (Path("/tmp"), Path("/var/tmp"), Path(tempfile.gettempdir()))
    for spelling in (path, path.resolve()):
        if any(character in str(spelling) for character in (",", "\n", "\r")):
            raise ValueError(f"Codex runtime cannot be expressed as a Docker bind mount: {path!s}")
        if any(spelling.is_relative_to(root) for root in (Path("/tmp"), Path("/tmp").resolve())):
            raise ValueError(f"Codex runtime is under hidden /tmp: {path}; use an installation outside /tmp")
        for root in forbidden:
            root = root.resolve()
            if spelling.is_relative_to(root) or (directory and root.is_relative_to(spelling)):
                raise ValueError(f"Codex runtime overlaps the platform or candidate: {path}")
        if not directory:
            continue
        if any(root.resolve().is_relative_to(spelling) or root.is_relative_to(spelling) for root in roots):
            raise ValueError(f"Codex runtime exposes a shared temporary root: {path}")
        home = Path.home()
        broad_home = (spelling == home or home.is_relative_to(spelling)
                      or spelling.parent in (Path("/home"), Path("/Users"))
                      or spelling.name in (".codex", ".ssh", ".config"))
        broad_system = (spelling in SYSTEM or spelling in
                        (Path("/usr/local"), Path("/usr/local/bin"), Path("/usr/local/lib"))
                        or len(spelling.parts) < 3
                        or (spelling.parts[1:2] == ("mnt",) and len(spelling.parts) <= 3))
        if broad_home or broad_system:
            raise ValueError(f"Codex runtime needs a dedicated installation directory: {path}; bind individual files instead")


def _package(path: Path) -> Path | None:
    for parent in path.parents:
        manifest = parent / "package.json"
        if manifest.is_file():
            try:
                if json.loads(manifest.read_text()).get("name") == "@openai/codex":
                    return parent
            except (OSError, ValueError):
                pass
    return None


def _native_elf(path: Path) -> bool:
    """Recognize a Linux executable image without trusting its filename or running it."""
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        with path.open('rb') as stream:
            header = stream.read(64)
        if (len(header) < 52 or header[:4] != b'\x7fELF' or header[4] not in (1, 2)
                or header[5] not in (1, 2) or header[6] != 1):
            return False
        order = '<' if header[5] == 1 else '>'
        kind, machine, version = struct.unpack_from(order + 'HHI', header, 16)
        if kind not in (2, 3) or not machine or version != 1:
            return False
        if header[4] == 2:
            if len(header) < 64:
                return False
            offset = struct.unpack_from(order + 'Q', header, 32)[0]
            size, entry_size, count = struct.unpack_from(order + 'HHH', header, 52)
            expected_size, expected_entry = 64, 56
        else:
            offset = struct.unpack_from(order + 'I', header, 28)[0]
            size, entry_size, count = struct.unpack_from(order + 'HHH', header, 40)
            expected_size, expected_entry = 52, 32
        return (size == expected_size and entry_size == expected_entry and count > 0
                and offset >= size and offset + count * entry_size <= path.stat().st_size)
    except (OSError, ValueError, struct.error):
        return False


def _variant(argv: list[str]) -> str:
    program = Path(argv[0])
    if program.name == 'codex' and _native_elf(program) and len(argv) == 1:
        return 'native'
    if (program.name.startswith('node') and _native_elf(program) and len(argv) == 2
            and _argument_file(argv[1]) is not None and _package(Path(argv[1]).resolve()) is not None):
        return 'npm'
    return 'opaque'


def content_inventory(bindings: list[Path]) -> dict:
    """Content identity for supported native/npm installations, including their links."""
    roots = sorted(set(bindings), key=lambda p: (len(p.parts), str(p)))
    roots = [root for i, root in enumerate(roots) if not any(
        parent.is_dir() and root.is_relative_to(parent) for parent in roots[:i])]
    rows = []
    counts = {'files': 0, 'directories': 0, 'symlinks': 0}

    def add(path):
        mode = path.lstat().st_mode
        row = [str(path), stat.S_IMODE(mode)]
        if stat.S_ISLNK(mode):
            target = path.resolve(strict=True)
            if not any(target == root or (root.is_dir() and target.is_relative_to(root)) for root in bindings):
                raise ValueError(f'Codex dependency link needs an explicit --runtime binding: {path}; {REMEDY}')
            if not target.is_file() and not target.is_dir():
                raise ValueError(f'Codex dependency link has a special target: {path}')
            row += ['symlink', os.readlink(path), str(target), digest(target) if target.is_file() else None]
            counts['symlinks'] += 1
        elif stat.S_ISREG(mode):
            row += ['file', digest(path)]
            counts['files'] += 1
        elif stat.S_ISDIR(mode):
            row += ['directory']
            counts['directories'] += 1
        else:
            raise ValueError(f'Codex runtime contains a special file: {path}')
        rows.append(row)

    def failed_walk(error):
        raise error

    for root in roots:
        add(root)
        if root.is_dir() and not root.is_symlink():
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed_walk):
                dirs[:] = sorted(name for name in dirs if name != '.git')
                for name in sorted([*dirs, *files]):
                    add(Path(directory) / name)
    payload = json.dumps(sorted(rows), ensure_ascii=True, separators=(',', ':')).encode()
    return {'sha256': hashlib.sha256(payload).hexdigest(), **counts,
            'excluded_directories': ['.git'], 'system': platform.system(), 'machine': platform.machine()}


def installation(argv: list[str], *, extra: tuple[Path, ...] = (),
                 forbidden: tuple[Path, ...] = ()) -> dict:
    """Derive installation bindings without collecting personal configuration."""
    bindings: list[Path] = []
    files: list[Path] = []

    def bind(path: Path):
        path = Path(os.path.abspath(path))
        validate_binding(path, forbidden=forbidden)
        if path not in bindings:
            bindings.append(path)
        # Preserve aliases used by scripts, and supply their physical targets.
        real = path.resolve()
        if real != path:
            validate_binding(real, forbidden=forbidden)
            if real not in bindings:
                bindings.append(real)

    def pin(path: Path):
        validate_binding(path, forbidden=forbidden)
        if path not in files:
            files.append(path)

    program = Path(argv[0])
    pin(program)
    prefix = program.parent.parent if program.parent.name == "bin" else program.parent
    venv = prefix / "pyvenv.cfg"
    if venv.is_file() or (prefix / "conda-meta").is_dir():
        bind(prefix)
        if venv.is_file():
            pin(venv)
        real = program.resolve()
        base = real.parent.parent if real.parent.name == "bin" else real.parent
        if not _system(base) and base != prefix.resolve():
            bind(base)
    elif "python" in program.name:
        standard_library = any((prefix / "lib").glob("python*/encodings/__init__.py"))
        bind(prefix if standard_library and not _system(prefix) else program)
    elif program.name.startswith("node") or (len(argv) > 1 and
            Path(argv[1]).suffix in (".js", ".mjs", ".cjs")):
        if _system(prefix):
            raise ValueError("Codex Node wrappers require a dedicated Node installation; select it with --node or --command")
        bind(prefix)
    else:
        helpers = [program.with_name(name) for name in ("codex-exec-server", "codex-code-mode-host")
                   if program.with_name(name).is_file()]
        if helpers and program.parent.name == "bin" and not _system(program.parent):
            bind(program.parent)
        else:
            # A single binary in HOME or /usr/bin needs only a file bind.
            bind(program)
            for path in helpers:
                bind(path)
        for path in helpers:
            pin(path)
        interpreter = _shebang(program)
        if interpreter and Path(interpreter[0]).is_absolute() and not _system(Path(interpreter[0])):
            bind(Path(interpreter[0]))
            pin(Path(interpreter[0]))

    for value in argv[1:]:
        path = _argument_file(value)
        if path is None or not path.is_absolute():
            continue
        bind(path)
        pin(path)
        package = _package(path.resolve())
        if package is not None:
            bind(package)
            pin(package / "package.json")
            metadata = json.loads((package / "package.json").read_text())
            dependencies = {**metadata.get("dependencies", {}), **metadata.get("optionalDependencies", {})}
            # npm can hoist a native optional package outside the wrapper tree.
            for name in dependencies:
                if not name.startswith("@openai/codex-") or ".." in Path(name).parts:
                    continue
                for parent in (package, *package.parents):
                    dependency = parent / "node_modules" / name
                    if dependency.is_dir():
                        bind(dependency)
                        pin(dependency / "package.json")
                        for binary in dependency.rglob("*"):
                            if binary.is_file() and binary.name in (
                                    "codex", "codex-exec-server", "codex-code-mode-host", "rg"):
                                pin(binary)
                        break
            for binary in (package / "vendor").rglob("*"):
                if binary.is_file() and binary.name in (
                        "codex", "codex-exec-server", "codex-code-mode-host", "rg"):
                    pin(binary)
    for path in extra:
        path = path.expanduser()
        bind(path)
        if path.is_file():
            pin(Path(os.path.abspath(path)))
    # A companion can itself be a link out of the dedicated program directory.
    # Supply that exact target without widening the directory binding.
    for path in files:
        real = path.resolve()
        if not any(real == p or (p.is_dir() and real.is_relative_to(p)) for p in bindings):
            bind(real)
    variant = _variant(argv)
    return {"closure": variant, "cacheable": variant != 'opaque', "scope": SCOPE,
            "content_inventory": content_inventory(bindings) if variant != 'opaque' else None,
            "argv": argv, "runtime_paths": [str(p) for p in bindings],
            "files": [{"path": str(p), "realpath": str(p.resolve(strict=True)),
                       "sha256": digest(p)} for p in files],
            "path": list(dict.fromkeys(str(Path(p).parent) for p in argv
                                       if Path(p).is_absolute() and _argument_file(p) is not None))}


def check_files(record: dict) -> None:
    """Recheck physical reachability and identity before publication or launch.

    Older schema-1 receipts lack realpath. They retain byte checks but must still
    prove the current physical target is supplied by their existing bindings.
    """
    bindings = [Path(value) for value in record["runtime_paths"]]
    for entry in record["files"]:
        path = Path(entry["path"])
        real = path.resolve(strict=True)
        if not path.is_absolute() or not any(path == tree or
                (tree.is_dir() and path.is_relative_to(tree)) for tree in bindings):
            raise ValueError(f"Codex identity file is outside its runtime bindings: {path}; {REMEDY}")
        # Mount destinations retain their spellings. An alias bind does not
        # provide its newly selected target at that target's physical location.
        if not any(real == tree or
                (tree.is_dir() and real.is_relative_to(tree)) for tree in bindings):
            raise ValueError(f"Codex physical identity file is outside its runtime bindings: {path}; {REMEDY}")
        expected = entry.get("realpath")
        if expected is not None and (not isinstance(expected, str) or expected != str(real)):
            raise ValueError(f"prepared Codex physical runtime file changed: {path}; {REMEDY}")
        if digest(path) != entry["sha256"]:
            raise ValueError(f"prepared Codex runtime file changed: {path}; {REMEDY}")
    inventory = record.get('content_inventory')
    cacheable = record.get('cacheable', False)
    if (not isinstance(cacheable, bool) or record.get('scope', SCOPE) != SCOPE
            or record.get('closure', _variant(record['argv'])) != _variant(record['argv'])
            or (record.get('cacheable') is True and
                (_variant(record['argv']) == 'opaque' or not isinstance(inventory, dict)))):
        raise ValueError(f'invalid prepared Codex content identity; {REMEDY}')
    if inventory is not None and (not isinstance(inventory, dict) or
                                  content_inventory(bindings) != inventory):
        raise ValueError(f'prepared Codex dependency contents changed; {REMEDY}')


def resolve(harness: Path, env: dict, *, forbidden: tuple[Path, ...] = ()) -> dict:
    """Use the prepared argv and exact file identities; never discover a new CLI."""
    require_clean_startup(env)
    manifest = json.loads((harness / "harness.json").read_text())
    prepared = manifest.get(RECORD)
    if not isinstance(prepared, dict) or prepared.get("schema") != 1:
        raise ValueError(f"Codex Harness is not prepared; {REMEDY}")
    argv = prepared.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(p, str) for p in argv):
        raise ValueError(f"invalid prepared Codex command; {REMEDY}")
    files = prepared.get("files")
    bindings = prepared.get("runtime_paths")
    if (not isinstance(files, list) or not files or not isinstance(bindings, list)
            or not bindings or not all(isinstance(p, str) for p in bindings)
            or not isinstance(prepared.get("version"), str)
            or not prepared["version"].strip() or not isinstance(prepared.get("path"), list)
            or not all(isinstance(p, str) for p in prepared["path"])
            or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str)
                       and isinstance(entry.get("sha256"), str) for entry in files)
            or argv[0] not in {entry["path"] for entry in files}):
        raise ValueError(f"invalid prepared Codex identity; {REMEDY}")
    pinned = {entry["path"] for entry in files}
    if len(pinned) != len(files) or any(Path(value).is_absolute() and _argument_file(value) is not None
                                       and value not in pinned for value in argv):
        raise ValueError(f"Codex command has an unrecorded runtime file; {REMEDY}")
    if env.get("HG_AGENT_CODEX_COMMAND", "").strip() or env.get("HG_AGENT_CODEX"):
        # Matching bare/relative choices keep their original discovery result,
        # even after PATH or cwd changes. A different override must resolve to it.
        if choice(env) != prepared.get("selection") and select(env) != argv:
            raise ValueError(f"Codex command override differs from the prepared installation; {REMEDY}")
    if manifest.get("runtime_paths") != prepared.get("runtime_paths"):
        raise ValueError(f"Codex runtime bindings differ from the prepared installation; {REMEDY}")
    for entry in bindings:
        validate_binding(Path(entry), forbidden=forbidden)
    check_files(prepared)
    executable(argv[0], {"PATH": ""})
    return prepared
