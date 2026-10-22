"""Compatibility alias for :mod:`eval.run.runner`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.run import runner as _implementation

_sys.modules[__name__] = _implementation
