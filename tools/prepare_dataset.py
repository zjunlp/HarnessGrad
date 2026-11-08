#!/usr/bin/env python3
"""Prepare an opted-in dataset provider or execution runtime.

Only this explicit preparation command installs dependencies. ``--check`` never
installs or downloads anything. Provider checks include data loading; execution
checks only verify the runtime artifact, without launching services or scoring.
Neither profile claims that a benchmark trial or its private scorer has run.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessgrad import dataset_runtime
from harnessgrad.dataset_provider import DatasetProvider


@contextmanager
def _runtime_root(root):
    """Apply one invocation's store selection to the provider client as well."""
    key = "HG_DATASET_RUNTIME_ROOT"
    previous = os.environ.get(key)
    if root is not None:
        os.environ[key] = str(root)
    try:
        yield
    finally:
        if root is not None:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--runtime", choices=("provider", "execution"), default="provider",
                        help="prepare/check provider loading or the execution artifact")
    parser.add_argument("--check", action="store_true", help="check without installing")
    parser.add_argument("--python", help="base interpreter for explicit preparation")
    parser.add_argument("--runtime-root", type=Path, help="external dataset runtime store")
    parser.add_argument("--timeout", type=float, help="optional preparation/provider timeout in seconds")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.check and args.python:
        parser.error("--python is only used when preparing a runtime")
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error("--timeout must be finite and positive")

    execution = args.runtime == "execution"
    runtime_scope = "dataset-execution-runtime" if execution else "dataset-provider-runtime"
    preparation_scope = runtime_scope if execution else "dataset-runtime-preparation"
    result = {"dataset": args.dataset, "profile": args.runtime,
              "action": "check" if args.check else "prepare"}
    try:
        with _runtime_root(args.runtime_root):
            runtime_dataset = args.dataset
            if execution:
                from data.scaffolds.registry import execution_runtime_name
                runtime_dataset = execution_runtime_name(args.dataset) or args.dataset
                result["runtime_dataset"] = runtime_dataset
            descriptor = dataset_runtime.execution_descriptor if execution else dataset_runtime.descriptor
            if descriptor(runtime_dataset) is None:
                kind = "execution runtime" if execution else "runtime"
                raise ValueError(f"{args.dataset}: no dataset {kind} declaration")
            if not args.check:
                prepare = dataset_runtime.prepare_execution if execution else dataset_runtime.prepare
                result["runtime"] = prepare(
                    runtime_dataset, python=args.python, root=args.runtime_root,
                    timeout_s=args.timeout)
            readiness = (dataset_runtime.check_execution(runtime_dataset, root=args.runtime_root)
                         if execution else DatasetProvider(args.dataset, timeout_s=args.timeout).check())
            result.update(readiness)
            result["dataset"] = args.dataset
            if execution:
                result["runtime_dataset"] = runtime_dataset
        code = 0 if result.get("present") is True and result.get("status") == "ready" else 1
    except dataset_runtime.DatasetRuntimeUnavailable as exc:
        result.update(present=False, status=exc.status, scope=runtime_scope, detail=str(exc))
        code = 1
    except subprocess.TimeoutExpired as exc:
        result.update(present=False, status="unavailable", scope=preparation_scope, detail=str(exc))
        code = 1
    except (ValueError, OSError) as exc:
        result.update(present=False, status="invalid" if isinstance(exc, ValueError) else "unavailable",
                      scope=preparation_scope, detail=str(exc))
        code = 2 if isinstance(exc, ValueError) else 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['status']} {args.dataset} ({result['scope']}): {result.get('detail', '')}")
        if execution and result.get("runtime_dataset"):
            print(f"  execution profile: {result['runtime_dataset']}")
        if result.get("runtime"):
            print(f"  runtime: {result['runtime']['path']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
