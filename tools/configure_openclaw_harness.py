#!/usr/bin/env python3
"""Prepare a pinned OpenClaw installation and atomically record its sandbox bindings."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessgrad.env_file import read_env_file
from harnessgrad.paths import main_checkout
from eval.containers import sandbox

TEMPLATE = ROOT / "base_harness/openclaw"


@dataclass(frozen=True)
class Release:
    commit: str
    integrity: str
    adapter_profile: str | None = None
    rebuild: bool = False


RELEASES = {
    "2026.3.13": Release("61d171a", "sha512-/juSUb070Xz8K8CnShjaZQr7CVtRaW4FbR93lgr1hLepcRSbyz2PQR+V4w5giVWkea61opXWPA6Vb8dybaztFg=="),
    "2026.11.2": Release("b556fdf", "sha512-SgBml4WLp8V1DdAYkrTPLzVdA/iYo3iuHCtm4788GjaG83QZdd1VeOTlue0HBJGdn3SbFE06b6uPs+AM5ShMgg==",
                         adapter_profile="entries", rebuild=True),
}


def latest_verified_version() -> str:
    return max(RELEASES, key=lambda version: tuple(map(int, version.split("."))))


def source_identity(version: str) -> dict:
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{4}\.[0-9]+\.[0-9]+", version):
        raise ValueError("OpenClaw version must be an exact YYYY.M.D release")
    release = RELEASES.get(version)
    expected = json.loads((TEMPLATE / "runtime-identity.json").read_text(encoding="utf-8"))
    if release is None:
        profile = RELEASES[latest_verified_version()].adapter_profile or "list"
        expected.update(version=version, upstream_commit=None, adapter_profile=profile, verified=False)
        return expected
    expected.update(version=version, upstream_commit=release.commit)
    if release.adapter_profile is not None:
        expected["adapter_profile"] = release.adapter_profile
    return expected


def warn_unverified(identity: dict) -> None:
    if identity.get("verified") is False:
        print(f"Warning: OpenClaw {identity['version']} is unverified. Trying the "
              f"{identity['adapter_profile']} adapter profile.", file=sys.stderr)


def _helper(harness: Path):
    for name in ("harness.json", "runtime-identity.json", "agent.py", "openclaw_runtime.py"):
        path = harness / name
        if not path.is_file() or not path.resolve().is_relative_to(harness):
            raise ValueError(f"OpenClaw candidate needs its owned {name}")
    # The candidate is data here. Its Python code runs only inside the task sandbox.
    path = TEMPLATE / "openclaw_runtime.py"
    spec = importlib.util.spec_from_file_location("_prepare_openclaw_runtime", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _environment() -> dict:
    primary = main_checkout(ROOT)
    env = read_env_file(primary / ".env")
    if primary != ROOT:
        env.update(read_env_file(ROOT / ".env"))
    env.update(os.environ)
    return env


def _additional_paths(env: dict) -> tuple[Path, ...]:
    raw = env.get("HG_OPENCLAW_RUNTIME_PATHS", "").strip()
    if not raw:
        return ()
    try:
        paths = json.loads(raw)
    except ValueError as exc:
        raise ValueError("HG_OPENCLAW_RUNTIME_PATHS must be a JSON array of path strings") from exc
    if not isinstance(paths, list) or not all(isinstance(p, str) and p.strip() for p in paths):
        raise ValueError("HG_OPENCLAW_RUNTIME_PATHS must be a JSON array of path strings")
    return tuple(Path(p) for p in paths)


def _probe(selected: dict, *, native: bool) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="hg-openclaw-probe-") as directory:
        root = Path(directory)
        work, task = root / "work", root / "task"
        work.mkdir()
        task.mkdir()
        env = {"PATH": os.pathsep.join((str(Path(selected["node"]).parent), os.defpath)),
               "PYTHONDONTWRITEBYTECODE": "1", "NODE_DISABLE_COMPILE_CACHE": "1",
               "OPENCLAW_NO_BANNER": "1"}
        for variable, folder in {"HOME": "home", "USERPROFILE": "home", "XDG_CONFIG_HOME": "config",
                                 "XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache", "XDG_STATE_HOME": "state",
                                 "TMPDIR": "tmp", "TEMP": "tmp", "TMP": "tmp"}.items():
            target = task / folder
            target.mkdir(exist_ok=True)
            env[variable] = str(target)
        state = task / "openclaw-state"
        state.mkdir()
        config = state / "openclaw.json"
        config.write_text("{}\n", encoding="utf-8")
        config.chmod(0o600)
        env.update({"OPENCLAW_STATE_DIR": str(state), "OPENCLAW_CONFIG_PATH": str(config),
                    "OPENCLAW_AGENT_DIR": str(state / "agents/main/agent")})
        outputs = []
        for argv in ([selected["node"], "--version"], [*selected["argv"], "--version"]):
            if native:
                argv = sandbox.command(argv, platform=ROOT, work_root=work, task_dir=task,
                                       repo=work, env=env, network="none",
                                       runtime_paths=tuple(Path(p) for p in selected["runtime_paths"]))
            ran = subprocess.run(argv, cwd=task, env=env, input="", capture_output=True,
                                 text=True, encoding='utf-8', errors='strict', timeout=20)
            if ran.returncode or not ran.stdout.strip():
                where = "native sandbox" if native else "host"
                hint = (" The launcher can also report missing dist/entry for a missing dependency; "
                        "check installed npm dependencies or declare nonstandard dependencies with --runtime."
                        if "missing dist/entry" in ran.stderr else "")
                raise ValueError(f"OpenClaw version probe failed in {where}: {ran.stderr.strip()[-1000:]}{hint}")
            outputs.append(ran.stdout.strip())
        if native:
            # Version flags can skip linked dependencies. Verify that the complete
            # recorded projection survives bwrap's mount/symlink semantics too.
            # This is trusted platform source, never candidate Python code.
            code = (ROOT / "base_harness/openclaw/openclaw_runtime.py").read_text(encoding="utf-8")
            code += "\ncheck_files(json.loads(__import__('sys').stdin.read()))\n"
            argv = sandbox.command([str(Path(sys.executable).resolve()), "-c", code],
                                   platform=ROOT, work_root=work, task_dir=task, repo=work,
                                   env=env, network="none",
                                   runtime_paths=tuple(Path(p) for p in selected["runtime_paths"]))
            ran = subprocess.run(argv, cwd=task, env=env, input=json.dumps(selected),
                                 capture_output=True, text=True, encoding='utf-8', errors='strict', timeout=20)
            if ran.returncode:
                raise ValueError("OpenClaw runtime projection failed in native sandbox; use physical dependency paths: "
                                 + ran.stderr.strip()[-1000:])
        return tuple(outputs)


def configure(harness: Path, openclaw: str | None = None, *, node: str | None = None,
              runtime_paths: tuple[Path, ...] | None = None, env: dict | None = None,
              version: str | None = None, warn: bool = True) -> dict:
    harness = harness.expanduser().resolve()
    helper = _helper(harness)
    path = harness / "harness.json"
    original = path.read_bytes()
    manifest = json.loads(original)
    if manifest.get("name") != "openclaw" or manifest.get("entrypoint") != "agent.py":
        raise ValueError("expected the OpenClaw agent.py manifest")
    candidate = helper.identity(harness)
    declared = source_identity(candidate["version"])
    if candidate != declared:
        raise ValueError("OpenClaw candidate source identity differs from the supported template")
    expected = declared if version is None else source_identity(version)
    if warn:
        warn_unverified(expected)
    selected_env = _environment() if env is None else env
    extra = _additional_paths(selected_env) if runtime_paths is None else runtime_paths
    selected = helper.installation(selected_env,
                                   openclaw=openclaw, node=node, extra=extra,
                                   forbidden=(ROOT, main_checkout(ROOT), harness))
    package = json.loads((Path(selected["package_root"]) / "package.json").read_text(encoding="utf-8"))
    if package.get("name") != "openclaw" or package.get("version") != expected["version"]:
        raise ValueError(f"OpenClaw version mismatch: expected {expected['version']}, found {package.get('version')}. "
                         "Select the intended version with --version.")
    # Unverified launchers run only in the network-isolated sandbox. In particular,
    # a package's first --version invocation can execute pending lifecycle scripts.
    unverified = expected.get("verified") is False
    probe = _probe(selected, native=unverified)
    node_version = helper.node_version(probe[0])
    version_output = helper.cli_version(probe[1], expected["version"])
    if not unverified and _probe(selected, native=True) != probe:
        raise ValueError("OpenClaw host and native sandbox versions differ")
    helper.check_files(selected)
    if path.read_bytes() != original or helper.identity(harness) != candidate:
        raise ValueError("OpenClaw candidate manifest or source identity changed during preparation")
    for binding in selected["runtime_paths"]:
        helper.validate_binding(Path(binding), forbidden=(ROOT, main_checkout(ROOT), harness))
    record = {"schema": 1, "source_identity": expected, "version": expected["version"],
              "node_version": node_version, "version_output": version_output, **selected}
    if candidate != expected:
        record["template_identity"] = candidate
    manifest["runtime_paths"] = selected["runtime_paths"]
    manifest[helper.RECORD] = record
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=harness, prefix=".openclaw-manifest-",
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
    parser.add_argument("--openclaw", help="installed OpenClaw launcher; otherwise use HG_AGENT_OPENCLAW or PATH")
    parser.add_argument("--node", help="Node executable; otherwise use HG_OPENCLAW_NODE or PATH")
    parser.add_argument("--version", help="exact CLI version to bind; defaults to the copy's declared version")
    parser.add_argument("--runtime", type=Path, action="append",
                        help="additional dependency, bound read-only (repeatable); overrides HG_OPENCLAW_RUNTIME_PATHS")
    args = parser.parse_args(argv)
    try:
        receipt = configure(args.harness, args.openclaw, node=args.node, version=args.version,
                            runtime_paths=tuple(args.runtime) if args.runtime is not None else None)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, sandbox.SandboxUnavailable) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
