#!/usr/bin/env python3
"""Public source self-tests, with no provider credentials or network access.

This is developer feedback, never the platform's independent acceptance result.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess

try:
    from .bun_workspace import source_for
except ImportError:
    from bun_workspace import source_for
from eval.ckpt.bun_dependencies import _module_dirs


def command(candidate, receipt, mode, package, tests):
    source = source_for(candidate, receipt)
    if source is None:
        raise ValueError("Self-test requires a declared Bun runtime")
    where = (source / package).resolve()
    if not where.is_relative_to(source) or not (where / "package.json").is_file():
        raise ValueError("Self-test package must belong to candidate source")
    bun, store = Path(receipt["binary"]), Path(receipt["dependency_store"])
    args = [str(bun), "--no-env-file", "--no-install"]
    if mode == "unit":
        if not tests:
            raise ValueError("Name at least one public test file")
        for name in tests:
            path = (where / name).resolve()
            if name.startswith("-") or not path.is_relative_to(where) or not path.is_file():
                raise ValueError("Self-test files must belong to the selected package")
        args += ["test", *tests]
    else:
        # core does not declare tsc itself. Reuse the compiler provided by its
        # installed workspaces; do not install a compiler or guess its version.
        compilers = {(m / ".bin/tsc").resolve() for m in _module_dirs(source)
                     if (m / ".bin/tsc").is_file()}
        local = where / "node_modules/.bin/tsc"
        compiler = local.resolve() if local.is_file() else next(iter(compilers)) if len(compilers) == 1 else None
        if compiler is None or not (compiler.is_relative_to(source) or compiler.is_relative_to(store)):
            raise ValueError("No unambiguous prepared tsc; typecheck unavailable")
        configs = [name for name in ("tsconfig.json", "tsconfig.tests.json") if (where / name).is_file()]
        if not configs:
            raise ValueError("No package TypeScript configuration")
        args += [str(compiler), "-b", *configs]
    wrapped = ["bwrap", "--die-with-parent", "--unshare-net", "--unshare-pid", "--tmpfs", "/tmp"]
    for root in ("/usr", "/lib", "/lib64", "/bin"):
        if Path(root).exists():
            wrapped += ["--ro-bind", root, root]
    for root in (bun.parent, store):
        wrapped += ["--ro-bind", str(root), str(root)]
    # No parent workspace, DSH home, platform, private tests, or host HOME mounts.
    wrapped += ["--bind", str(candidate), str(candidate),
                "--ro-bind", str(candidate / ".state"), str(candidate / ".state"),
                "--dev", "/dev", "--proc", "/proc", "--clearenv",
                "--setenv", "PATH", f"{bun.parent}:/usr/bin:/bin",
                "--setenv", "HOME", "/tmp", "--setenv", "CI", "true",
                "--chdir", str(where), "--", *args]
    return wrapped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True,
                        help="method-owned launch receipt outside candidate source")
    parser.add_argument("mode", choices=("unit", "typecheck"))
    parser.add_argument("package", help="source-relative package, e.g. packages/core")
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args(argv)
    candidate = args.candidate.resolve()
    if args.runtime.resolve().is_relative_to(candidate):
        raise ValueError("Self-test launch receipt must be outside the candidate")
    receipt = json.loads(args.runtime.read_text())
    argv = command(candidate, receipt, args.mode, args.package, args.tests)
    with subprocess.Popen(argv, env={"PATH": "/usr/bin:/bin"}, start_new_session=True) as proc:
        try:
            return proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            print("Self-test timed out after 120 seconds; no passing result.", flush=True)
            return 124


if __name__ == "__main__":
    raise SystemExit(main())
