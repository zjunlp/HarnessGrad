#!/usr/bin/env python3
"""Explicit real-wheel preparation for the shipped Loop integration controls."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, required=True,
                        help='external uv cache containing the genuine locked wheels')
    parser.add_argument('--store', type=Path, required=True, help='external managed runtime store')
    parser.add_argument('--wheelhouse', type=Path, help='directory of original genuine wheel files')
    parser.add_argument('--allow-downloads', action='store_true',
                        help='explicitly allow uv to fetch public locked wheels (default: offline)')
    args = parser.parse_args()
    os.environ['UV_CACHE_DIR'] = str(args.cache_dir.resolve())
    os.environ['UV_PYTHON_DOWNLOADS'] = 'never'
    os.environ['HG_REVIEWED_ENVIRONMENT_ONLY'] = '1'
    if args.wheelhouse:
        os.environ['HG_HARNESS_WHEELHOUSE'] = str(args.wheelhouse.resolve())
    from harnessgrad import config, python_exec_runtime
    config.reset()
    result = python_exec_runtime.prepare(ROOT / 'base_harness/loop', build=True,
        root=args.store.resolve(), allow_downloads=args.allow_downloads)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
