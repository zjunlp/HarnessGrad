"""Compatibility alias for :mod:`eval.policy.gaia_snapshot_attestation`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
import sys as _sys
from eval.policy import gaia_snapshot_attestation as _implementation

_sys.modules[__name__] = _implementation
