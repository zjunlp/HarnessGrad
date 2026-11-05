#!/usr/bin/env python3
"""Select an installed OpenCode v1 source tree and Bun, without installing dependencies."""
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
from eval.ckpt.source_tree import runtime_spec
from eval.containers import sandbox


def _helper(harness: Path):
    for name in ("harness.json", "agent.py", "opencode_runtime.py"):
        path = harness / name
        if not path.is_file() or not path.resolve().is_relative_to(harness):
            raise ValueError(f"OpenCode candidate needs its owned {name}")
    path = ROOT / "base_harness/opencode/opencode_runtime.py"
    spec = importlib.util.spec_from_file_location("_prepare_opencode_runtime", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _environment():
    primary = main_checkout(ROOT)
    env = read_env_file(primary / ".env")
    if primary != ROOT:
        env.update(read_env_file(ROOT / ".env"))
    env.update(os.environ)
    return env


def _probe(selected: dict) -> str:
    # Candidate/source code is never executed in the host namespace.
    with tempfile.TemporaryDirectory(prefix="hg-opencode-probe-") as directory:
        root = Path(directory)
        work, task = root / "work", root / "task"
        work.mkdir(); task.mkdir()
        env = {"PATH": os.pathsep.join((str(Path(selected["binary"]).parent), os.defpath)),
               "PYTHONDONTWRITEBYTECODE": "1", "CI": "true",
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
               "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
               "OPENCODE_CONFIG_CONTENT": '{"plugin":[],"share":"disabled","autoupdate":false}'}
        for name, folder in {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
                             "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "state", "TMPDIR": "tmp",
                             "TMP": "tmp", "TEMP": "tmp", "OPENCODE_TEST_HOME": "home"}.items():
            path = task / folder
            path.mkdir(exist_ok=True)
            env[name] = str(path)
        package = Path(selected["entrypoint"]).parent.parent
        tsconfig = package / "tsconfig.json"
        if tsconfig.is_file():
            data = json.loads(tsconfig.read_text())
            options = dict(data.get("compilerOptions") or {})
            if options.get("paths"):
                options["paths"] = {alias: [str((package / p).resolve()) for p in paths]
                                    for alias, paths in options["paths"].items()}
            (task / "tsconfig.json").write_text(json.dumps({"compilerOptions": options}))
        solid = package / "node_modules/@opentui/solid/scripts/preload.ts"
        if not solid.is_file():
            solid = Path(selected["source"]) / "node_modules/@opentui/solid/scripts/preload.ts"
        (task / "bunfig.toml").write_text('preload = [' + json.dumps(str(solid.resolve())) + ']\n' if solid.is_file() else '')
        preload = task / "channel.ts"
        preload.write_text("globalThis.OPENCODE_VERSION = " + json.dumps(selected["source_version"]) +
                           "\nglobalThis.OPENCODE_CHANNEL = " + json.dumps(selected["source_version"]) + "\n")
        def run(argv, payload=""):
            command = sandbox.command(argv, platform=ROOT, work_root=work, task_dir=task, repo=work,
                                      env=env, network="none", runtime_paths=tuple(Path(p) for p in selected["runtime_paths"]))
            result = subprocess.run(command, cwd=task, env=env, input=payload, capture_output=True,
                                    text=True, errors="replace", timeout=60)
            if result.returncode:
                raise ValueError("OpenCode native preparation probe failed: " + result.stderr.strip()[-1800:])
            return result.stdout.strip()
        bun = run([selected["binary"], "--no-env-file", "--no-install", "--version"])
        version = run([selected["binary"], "--no-env-file", "--no-install", "--no-macros",
                       "--preload", str(preload), selected["entrypoint"], "--version"])
        if version != selected["source_version"] or not bun:
            raise ValueError(f"OpenCode/Bun version probe mismatch: expected {selected['source_version']!r}, got {version!r}")
        code = (ROOT / "base_harness/opencode/opencode_runtime.py").read_text()
        run([str(Path(sys.executable).resolve()), "-c", code + "\ncheck(json.loads(__import__('sys').stdin.read()))\n"],
            json.dumps(selected))
        return bun


def configure(harness: Path, source: str | None = None, *, bun: str | None = None,
              runtime_paths: tuple[Path, ...] = (), env: dict | None = None) -> dict:
    harness = harness.expanduser().resolve()
    helper = _helper(harness)
    path = harness / "harness.json"
    original = path.read_bytes()
    manifest = json.loads(original)
    declaration = runtime_spec(harness)
    if manifest.get("name") != "opencode" or declaration is None or declaration["package"] != "opencode":
        raise ValueError("expected the OpenCode Bun source Harness")
    record = helper.installation(harness, declaration, _environment() if env is None else env,
                                 source=source, bun=bun, extra=runtime_paths, forbidden=(ROOT, main_checkout(ROOT)))
    record["bun_version"] = _probe(record)
    helper.check(record)
    if path.read_bytes() != original:
        raise ValueError("OpenCode manifest changed during preparation")
    manifest["runtime_paths"] = record["runtime_paths"]
    manifest[helper.RECORD] = record
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=harness, prefix=".opencode-manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(manifest, indent=2) + "\n")
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {"harness": str(harness), **record}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", type=Path)
    parser.add_argument("--source", help="prepared external OpenCode v1 source, or HG_AGENT_OPENCODE_SOURCE")
    parser.add_argument("--bun", help="Bun executable; otherwise use HG_AGENT_OPENCODE_BUN, HG_BUN_BINARY or PATH")
    parser.add_argument("--runtime", action="append", type=Path, default=[], help="additional dedicated dependency (repeatable)")
    args = parser.parse_args(argv)
    try:
        result = configure(args.harness, args.source, bun=args.bun, runtime_paths=tuple(args.runtime))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, sandbox.SandboxUnavailable) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
