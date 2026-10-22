#!/usr/bin/env python3
"""Prepare declared Bun dependencies offline, outside task measurement.

Uses the existing Bun cache; never downloads packages or runs lifecycle scripts.
The manifest must explicitly declare its runtime. Frozen inputs are not migrated.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.containers.bun_runtime import configure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", type=Path)
    args = parser.parse_args(argv)
    receipt = configure(args.harness)
    if receipt is None:
        parser.error("harness.json must declare runtime.kind=bun")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
