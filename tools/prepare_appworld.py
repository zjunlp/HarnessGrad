"""Explicit AppWorld data materialization from a checkout or installed wheel."""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        from data.datasets.appworld import prepare
        print(prepare())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"AppWorld preparation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
