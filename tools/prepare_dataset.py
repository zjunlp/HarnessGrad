#!/usr/bin/env python3
"""Prepare an opted-in dataset provider, then check that its data is loadable.

Only this explicit preparation command installs dependencies. ``--check`` never
installs or downloads anything. A successful check describes provider loading;
it does not claim that a benchmark trial or its private scorer has run.
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
    parser.add_argument("--check", action="store_true", help="check without installing")
    parser.add_argument("--python", help="base interpreter for explicit preparation")
    parser.add_argument("--runtime-root", type=Path, help="external provider runtime store")
    parser.add_argument("--timeout", type=float, help="optional preparation/provider timeout in seconds")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.check and args.python:
        parser.error("--python is only used when preparing a runtime")
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error("--timeout must be finite and positive")

    result = {"dataset": args.dataset, "action": "check" if args.check else "prepare"}
    try:
        with _runtime_root(args.runtime_root):
            if dataset_runtime.descriptor(args.dataset) is None:
                raise ValueError(f"{args.dataset}: no dataset runtime declaration")
            if not args.check:
                result["runtime"] = dataset_runtime.prepare(
                    args.dataset, python=args.python, root=args.runtime_root,
                    timeout_s=args.timeout)
            readiness = DatasetProvider(args.dataset, timeout_s=args.timeout).check()
            result.update(readiness)
        code = 0 if result.get("present") is True and result.get("status") == "ready" else 1
    except dataset_runtime.DatasetRuntimeUnavailable as exc:
        result.update(present=False, status=exc.status, scope="dataset-provider-runtime", detail=str(exc))
        code = 1
    except subprocess.TimeoutExpired as exc:
        result.update(present=False, status="unavailable", scope="dataset-runtime-preparation", detail=str(exc))
        code = 1
    except (ValueError, OSError) as exc:
        result.update(present=False, status="invalid" if isinstance(exc, ValueError) else "unavailable",
                      scope="dataset-runtime-preparation", detail=str(exc))
        code = 2 if isinstance(exc, ValueError) else 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['status']} {args.dataset} ({result['scope']}): {result.get('detail', '')}")
        if result.get("runtime"):
            print(f"  runtime: {result['runtime']['path']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
