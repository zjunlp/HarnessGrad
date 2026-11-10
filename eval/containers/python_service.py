"""A read-only filesystem view for explicitly bound trusted Python services.

This is a dependency/state boundary for trusted service code. Actor admission and
its separate filesystem view remain the runner's responsibility.
"""
from __future__ import annotations

import os
from pathlib import Path
from harnessgrad.bubblewrap import executable as bubblewrap_executable


def environment(directory: Path, inherited: dict | None) -> dict:
    """Give one service private storage and only its explicit broker handles."""
    selected = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
                "HG_REVIEWED_ENVIRONMENT_ONLY": "1"}
    for key, name in (("HOME", "home"), ("TMPDIR", "tmp"),
                      ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
        path = directory / name
        path.mkdir(mode=0o700)
        selected[key] = str(path)
    for key in ("HG_RELAY_CONTEXT_PATH", "HG_RELAY_RECORD_PATH", "HG_ENV_MODEL_SOCKET"):
        if key in (inherited or {}):
            selected[key] = inherited[key]
    return selected


def command(argv: list[str], *, directory: Path, runtime_paths: list[str],
            read_paths: list[str], model_socket: str | None = None,
            info_fd: int | None = None) -> list[str]:
    """Mount declared inputs/runtime read-only and only this instance writable."""
    try:
        bwrap = bubblewrap_executable()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if not isinstance(read_paths, list) or any(not isinstance(p, str) for p in read_paths):
        raise ValueError("bound Python service read_paths must be absolute paths")
    directory = directory.resolve(strict=True)
    mounts = []
    broad = {Path("/"), Path("/tmp"), Path("/var/tmp"), Path("/home"), Path.home().resolve()}
    for value in [*runtime_paths, *read_paths]:
        path = Path(value)
        if (not path.is_absolute() or ".." in path.parts or path in broad
                or path.resolve(strict=True) != path
                or not (path.is_dir() or path.is_file())):
            raise ValueError(f"invalid bound Python service read path: {value}")
        if path == directory or path in directory.parents or directory in path.parents:
            raise ValueError("service read paths overlap its writable state")
        if path not in mounts:
            mounts.append(path)
    program = Path(argv[3])  # [absolute Python, -I, -B, absolute service program, ...]
    if not any(program == path or (path.is_dir() and path in program.parents) for path in mounts):
        raise ValueError("bound Python service program must be a declared read path")
    plan = [bwrap, "--die-with-parent", "--unshare-pid", "--as-pid-1", "--unshare-net",
            "--proc", "/proc", "--dev", "/dev"]
    if info_fd is not None:
        plan += ["--info-fd", str(info_fd)]
    for name in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
        path = Path(name)
        if path.exists():
            plan += ["--ro-bind", name, name]
    for path in sorted(mounts, key=lambda item: (len(item.parts), str(item))):
        plan += ["--ro-bind", str(path), str(path)]
    if model_socket is not None:
        # The broker owns inference credentials and transport; the service only
        # gets this socket, even though it has no network namespace egress.
        plan += ["--ro-bind", model_socket, model_socket]
    plan += ["--bind", str(directory), str(directory),
             "--chdir", str(directory / "state"), "--", *argv]
    return plan
