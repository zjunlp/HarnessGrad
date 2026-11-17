#!/usr/bin/env python3
"""Improver resolver CLI; implementation lives in harnessgrad.improvers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessgrad.improvers import resolver as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == '__main__':
    raise SystemExit(_implementation.main())
elif __name__ == 'tools.improver':
    sys.modules[__name__] = _implementation
