"""Compatibility alias for :mod:`eval.policy.gaia_preflight`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("eval.policy.gaia_preflight", run_name="__main__")
else:
    import sys as _sys
    from eval.policy import gaia_preflight as _implementation

    _sys.modules[__name__] = _implementation
