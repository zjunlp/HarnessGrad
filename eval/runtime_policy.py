"""Compatibility alias for :mod:`eval.policy.runtime_policy`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.policy import runtime_policy as _implementation

_sys.modules[__name__] = _implementation
