#!/usr/bin/env python3
"""Native research preparation CLI; implementation lives with the dataset scaffold."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.scaffolds.native_research import conversion as _implementation

main = _implementation.main


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == '__main__':
    raise SystemExit(main())
elif __name__ == 'tools.prepare_native_research':
    sys.modules[__name__] = _implementation
