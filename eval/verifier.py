"""Compatibility alias for :mod:`eval.verification.verifier`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.verification import verifier as _implementation

_sys.modules[__name__] = _implementation
