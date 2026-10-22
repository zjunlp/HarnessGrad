"""Compatibility alias for :mod:`eval.containers.harness_runtime`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("eval.containers.harness_runtime", run_name="__main__")
else:
    import sys as _sys
    from eval.containers import harness_runtime as _implementation

    _sys.modules[__name__] = _implementation
