#!/usr/bin/env python3
"""Prepare dynamic CostBench using its managed execution artifact."""
from pathlib import Path
import sys


def main(argv=None):
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    file = root / 'data/datasets/costbench/prepare_controller.py'
    namespace = {'__file__': str(file), '__name__': '_costbench_controller'}
    exec(compile(file.read_bytes(), str(file), 'exec'), namespace)
    return namespace['main'](argv, dynamic=True)


if __name__ == '__main__':
    raise SystemExit(main())
