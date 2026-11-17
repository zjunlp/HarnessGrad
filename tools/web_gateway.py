#!/usr/bin/env python3
"""Web gateway CLI and compatibility import; implementation is in eval.service."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.service import web_gateway as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == '__main__':
    raise SystemExit(_implementation.main())
elif __name__ == 'tools.web_gateway':
    sys.modules[__name__] = _implementation
