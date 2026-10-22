"""Compatibility alias for :mod:`eval.run.task_contract`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.run import task_contract as _implementation

_sys.modules[__name__] = _implementation
