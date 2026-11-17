#!/usr/bin/env python3
"""Harness validation CLI; implementation lives in the execution layer."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessgrad.execution import harness_validation as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == '__main__':
    raise SystemExit(_implementation.main())
elif __name__ == 'tools.validate_harness':
    # Existing importers and patch targets see the same module state.
    sys.modules[__name__] = _implementation
