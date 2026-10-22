"""Compatibility alias for :mod:`eval.run.integrity`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.run import integrity as _implementation

_sys.modules[__name__] = _implementation
