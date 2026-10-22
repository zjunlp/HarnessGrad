"""Compatibility alias for :mod:`eval.admission.lifecycle_receipt`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.admission import lifecycle_receipt as _implementation

_sys.modules[__name__] = _implementation
