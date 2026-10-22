"""Compatibility alias for :mod:`eval.service.separated_stdio`.

New code imports the canonical path. Module identity (including private names,
patch targets and mutable state) is shared; see docs/architecture.md.
"""
if __name__ == "__main__":
    import runpy as _runpy

    from pathlib import Path as _Path

    _runpy.run_path(str(_Path(__file__).resolve().parent / "service" / "separated_stdio.py"),
                    run_name="__main__")
else:
    import sys as _sys
    from eval.service import separated_stdio as _implementation

    _sys.modules[__name__] = _implementation
