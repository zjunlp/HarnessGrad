#!/usr/bin/env python3
"""Bind an installed OpenCode npm native executable."""
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

from harnessgrad.paths import main_checkout
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


def configure(harness: Path, binary: Path, *, expected_version: str | None = None) -> dict:
    harness = harness.expanduser().resolve()
    helper = _helper(harness)
    path = harness / "harness.json"
    original = path.read_bytes()
    manifest = json.loads(original)
    if manifest.get("name") != "opencode" or manifest.get("entrypoint") != "agent.py":
        raise ValueError("expected the OpenCode adapter manifest")
    forbidden = (ROOT, main_checkout(ROOT), harness)
    record = helper.installation(binary, forbidden=forbidden)
    version = record["version"]
    if expected_version is not None and version != expected_version:
        raise ValueError(f"OpenCode package version mismatch: expected {expected_version}, found {version}")
    with tempfile.TemporaryDirectory(prefix="hg-opencode-probe-") as temporary:
        root = Path(temporary)
        work, task = root / "work", root / "task"
        work.mkdir()
        task.mkdir()
        env = {"PATH": os.defpath, "HOME": str(task), "CI": "true",
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1"}
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            env[key] = str(task / key.lower())
        command = sandbox.command([*record["argv"], "--version"], platform=ROOT,
                                  work_root=work, repo=work, task_dir=task, env=env, network="none",
                                  runtime_paths=tuple(map(Path, record["runtime_paths"])))
        result = subprocess.run(command, cwd=task, env=env, input="", capture_output=True,
                                text=True, timeout=30)
        if result.returncode or result.stdout.strip() not in (version, f"opencode v{version}"):
            raise ValueError(f"OpenCode sandbox version probe failed for {version}: "
                             f"{(result.stderr or result.stdout)[-1200:]}")
    if path.read_bytes() != original or helper.installation(binary, forbidden=forbidden) != record:
        raise ValueError("OpenCode manifest or installation changed during preparation")
    manifest.pop("runtime", None)
    manifest["runtime_paths"] = record["runtime_paths"]
    manifest[helper.RECORD] = record
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=harness, prefix=".opencode-manifest-",
                                         encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(manifest, indent=2) + "\n")
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", type=Path)
    parser.add_argument("--opencode", type=Path, required=True, help="native executable in the npm package")
    args = parser.parse_args(argv)
    try:
        result = configure(args.harness, args.opencode)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
