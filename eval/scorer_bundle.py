"""Compatibility alias for :mod:`eval.admission.scorer_bundle`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("eval.admission.scorer_bundle", run_name="__main__")
else:
    import sys as _sys
    from eval.admission import scorer_bundle as _implementation

    _sys.modules[__name__] = _implementation
