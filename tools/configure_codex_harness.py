#!/usr/bin/env python3
"""Prepare one local Codex installation and record its sandbox bindings atomically."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessgrad.env_file import read_env_file
from harnessgrad.paths import main_checkout
from eval.containers import sandbox


def _adapter(harness: Path):
    for name in ("agent.py", "codex_runtime.py"):
        owned = harness / name
        if not owned.is_file() or not owned.resolve().is_relative_to(harness):
            raise ValueError(f"Codex candidate needs its owned {name}")
    # Preparation is trusted platform code. Never import the candidate here;
    # its own helper is executed only by the task inside the task sandbox.
    path = ROOT / "base_harness/codex/codex_runtime.py"
    spec = importlib.util.spec_from_file_location("_prepare_codex_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _environment() -> dict:
    primary = main_checkout(ROOT)
    values = read_env_file(primary / ".env")
    if primary != ROOT:
        values.update(read_env_file(ROOT / ".env"))
    values.update(os.environ)
    return values


def _version(selected: dict, *, native: bool) -> str:
    with tempfile.TemporaryDirectory(prefix="hg-codex-probe-") as directory:
        root = Path(directory)
        work = root / "work"
        task = root / "task"
        work.mkdir()
        task.mkdir()
        home = task / "home"
        home.mkdir()
        env = {"PATH": os.pathsep.join([*selected["path"], os.defpath]),
               "HOME": str(home), "CODEX_HOME": str(home),
               "XDG_CONFIG_HOME": str(home), "XDG_CACHE_HOME": str(home),
               "TMPDIR": str(task), "PYTHONDONTWRITEBYTECODE": "1"}
        argv = [*selected["argv"], "--version"]
        if native:
            argv = sandbox.command(argv, platform=ROOT, work_root=work, task_dir=task,
                                   repo=work, env=env, network="none",
                                   runtime_paths=tuple(Path(p) for p in selected["runtime_paths"]))
        result = subprocess.run(argv, cwd=task, env=env, input="", capture_output=True,
                                text=True, errors="replace", timeout=15)
        if result.returncode or not result.stdout.strip():
            where = "native sandbox" if native else "host"
            raise ValueError(f"Codex --version failed in {where}: {result.stderr.strip()[-1000:]}")
        return result.stdout.strip()


def configure(harness: Path, codex: str | None = None, *, command: str | None = None,
              node: str | None = None, runtime_paths: tuple[Path, ...] = (),
              env: dict | None = None) -> dict:
    harness = harness.expanduser().resolve()
    path = harness / "harness.json"
    manifest = json.loads(path.read_text())
    if manifest.get("name") != "codex" or manifest.get("entrypoint") != "agent.py":
        raise ValueError("expected the Codex agent.py manifest")
    adapter = _adapter(harness)
    selected_env = _environment() if env is None else env
    adapter.require_clean_startup(selected_env)
    argv = adapter.select(selected_env, codex=codex, command=command, node=node)
    selected = adapter.installation(argv, extra=runtime_paths,
                                    forbidden=(ROOT, main_checkout(ROOT), harness))
    version = _version(selected, native=False)
    if _version(selected, native=True) != version:
        raise ValueError("Codex host and native sandbox versions differ")
    record = {"schema": 1, "version": version,
              "selection": adapter.choice(selected_env, codex=codex, command=command), **selected}
    # Detect changes during probes before committing a complete installation.
    adapter.check_files(record)
    manifest["runtime_paths"] = selected["runtime_paths"]
    manifest[adapter.RECORD] = record
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=harness, prefix=".codex-manifest-",
                                         encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(manifest, indent=2) + "\n")
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"harness": str(harness), **record}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--codex", help="Codex executable; otherwise use HG_AGENT_CODEX or PATH")
    source.add_argument("--command", help="argv-style command; otherwise use HG_AGENT_CODEX_COMMAND")
    parser.add_argument("--node", help="interpreter for an npm Codex launcher")
    parser.add_argument("--runtime", type=Path, action="append", default=[],
                        help="additional wrapper dependency, bound read-only (repeatable)")
    args = parser.parse_args(argv)
    try:
        receipt = configure(args.harness, args.codex, command=args.command,
                            node=args.node, runtime_paths=tuple(args.runtime))
    except (OSError, ValueError, subprocess.SubprocessError, sandbox.SandboxUnavailable) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
