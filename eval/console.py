"""Compatibility alias for :mod:`eval.observe.console`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.observe import console as _implementation

_sys.modules[__name__] = _implementation
