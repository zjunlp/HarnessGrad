"""Compatibility alias for :mod:`eval.service.unix_model_relay`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.service import unix_model_relay as _implementation

_sys.modules[__name__] = _implementation
