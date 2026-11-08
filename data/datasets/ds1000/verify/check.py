#!/usr/bin/env python3
"""Bound DS-1000 official construction in the selected scientific interpreter.

The upstream executor source and private context are authenticated before use.
Its construction, disabled reliability_guard and 120-second budget stay intact.
Interpreter/dependency/transport failures are unmeasured, never ordinary zeroes.
"""
from __future__ import annotations

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("DS-1000 checks require Python isolated mode (-I)")

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import ModuleType


SCIENTIFIC_MODULES = ("numpy", "pandas", "scipy", "sklearn", "matplotlib", "seaborn", "torch")


def _trusted_search_path():
    task = Path.cwd().resolve()
    script = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path if not any(
        (path := Path(entry or ".").resolve()) == root or path.is_relative_to(root)
        for root in (task, script))]


def _source_module(name: str, path: Path):
    """Load exact source bytes, ignoring task packages, .pyc and symlink aliases."""
    path = path.absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("trusted scorer source must not traverse a symlink")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("trusted scorer source must be a regular file")
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def _write(path, payload):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")


def _cannot_measure(args, why, receipt=None):
    # Direct controls, as well as native dispatch, must never reuse an old score.
    Path(args.reward_file).unlink(missing_ok=True)
    _write(args.evidence_file, json.dumps({"scorer_input": receipt, "measured": False,
        "error": why, "metric": "DS-1000 official execution"}, sort_keys=True) + "\n")
    print(f"check cannot measure: {why}", file=sys.stderr)
    return 3


def _science_preflight():
    _trusted_search_path()
    try:
        import numpy
        import pandas
        import scipy
        import sklearn
        import matplotlib
        import seaborn
        import torch
        modules = dict(zip(SCIENTIFIC_MODULES,
                           (numpy, pandas, scipy, sklearn, matplotlib, seaborn, torch), strict=True))
        if modules["torch"].version.cuda is not None:
            raise ValueError("the declared DS-1000 profile requires CPU-only Torch")
    except Exception as exc:
        print(json.dumps({"available": False, "error": f"scientific runtime unavailable: {type(exc).__name__}"}))
        return 3
    print(json.dumps({"available": True}))
    return 0


def _official_child(execution_path, payload):
    _trusted_search_path()
    try:
        # Probe in a separate process. Scientific imports must not initialize
        # thread pools in the parent that upstream's multiprocessing forks.
        probe = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).absolute()),
                                "--science-preflight"], capture_output=True, text=True,
                               timeout=30, env=os.environ)
        readiness = json.loads(probe.stdout.strip().splitlines()[-1])
        if probe.returncode != 0 or readiness.get("available") is not True:
            print(json.dumps({"available": False, "error": readiness.get("error", "scientific runtime unavailable")}))
            return 3
        executor = _source_module("_ds1000_official_execution", Path(execution_path))
        program = json.loads(Path(payload).read_text(encoding="utf-8"))
        result = executor.check_correctness(program, timeout=120)
        print(json.dumps({"available": True, "result": result}))
        return 0
    except Exception as exc:
        print(json.dumps({"available": False, "error": f"official executor unavailable: {type(exc).__name__}"}))
        return 3


def main():
    _trusted_search_path()
    ap = argparse.ArgumentParser()
    ap.add_argument("--reward-file", default="verifier/reward.txt")
    ap.add_argument("--evidence-file", default="verifier/evidence.json")
    try:
        helper = _source_module("_ds1000_scorer_input", Path("data/scaffolds/scorer_input.py"))
    except (OSError, ValueError) as exc:
        args, _ = ap.parse_known_args()
        return _cannot_measure(args, f"trusted input helper unavailable: {type(exc).__name__}")
    helper.add_arguments(ap)
    args = ap.parse_args()
    if any(value is None for value in (args.task_id, args.scorer_input_sha256, args.scorer_input_files)):
        return _cannot_measure(args, "DS-1000 requires task-bound scorer inputs")
    # The helper emits the exact-inventory refusal for malformed JSON/paths/digests.
    paths = {"context.json": Path("context.json"), "execution.py": Path("execution.py")}
    try:
        declared = json.loads(args.scorer_input_files)
        if isinstance(declared, dict):
            paths.update({name: Path(name) for name in declared if name.startswith("seaborn-data/")})
    except ValueError:
        pass
    Path(args.reward_file).unlink(missing_ok=True)
    return helper.run_bound(args, paths, lambda inputs, receipt: _score(args, inputs, receipt))


def _score(args, inputs, receipt):
    try:
        task = json.loads(inputs["context.json"].read_text(encoding="utf-8"))
        solution = Path("solution.py")
        if solution.is_symlink():
            raise ValueError("candidate solution must not be a symlink")
        candidate = solution.read_text(encoding="utf-8") if solution.is_file() else ""
        program = (task["code_context"] + "\n" + f"code = {candidate!r}\n"
                   + "test_execution(code)\n"
                   + ("test_string(code)\n" if "test_string(" in task["code_context"] else ""))
        with tempfile.TemporaryDirectory(prefix="ds1000-check-") as folder:
            scratch = Path(folder)
            execution = scratch / "execution.py"
            execution.write_bytes(inputs["execution.py"].read_bytes())
            payload = scratch / "program.json"
            payload.write_text(json.dumps(program), encoding="utf-8")
            env = dict(os.environ)
            csv_paths = [path for name, path in inputs.items() if name.startswith("seaborn-data/")]
            env["SEABORN_DATA"] = str(csv_paths[0].parent if csv_paths else scratch / "seaborn-data")
            env.setdefault("MPLBACKEND", "Agg")
            env["MPLCONFIGDIR"] = str(scratch / "matplotlib-config")
            proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).absolute()),
                                   "--official-child", str(execution), str(payload)], cwd=scratch,
                                  capture_output=True, text=True, timeout=170, env=env)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        if not isinstance(result, dict):
            raise ValueError("invalid executor envelope")
        if result.get("available") is not True:
            return _cannot_measure(args, result.get("error", "scientific executor unavailable"), receipt)
        result = result["result"]
        if (not isinstance(result, dict) or proc.returncode != 0
                or type(result.get("passed")) is not bool or not isinstance(result.get("result"), str)):
            raise ValueError("invalid official result transport")
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as exc:
        return _cannot_measure(args, f"official check unavailable: {type(exc).__name__}", receipt)
    why = result["result"]
    reward = 1.0 if result["passed"] else 0.0
    _write(args.reward_file, f"{reward:g}\n")
    _write(args.evidence_file, json.dumps({"scorer_input": receipt, "measured": True,
        "reward": reward, "official": {"metric": "DS-1000 test_execution plus conditional test_string",
        "result": why}, "why": why}, sort_keys=True) + "\n")
    print(f"reward {reward:g}: {why}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--science-preflight"]:
        raise SystemExit(_science_preflight())
    if sys.argv[1:2] == ["--official-child"]:
        raise SystemExit(_official_child(*sys.argv[2:]))
    raise SystemExit(main())
