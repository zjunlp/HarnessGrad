"""Retained command entrypoint; Python imports use the canonical module."""
if __name__ == "__main__":
    import runpy as _runpy

    from pathlib import Path as _Path

    _runpy.run_path(str(_Path(__file__).resolve().parent / "service" / "separated_stdio.py"),
                    run_name="__main__")
else:
    raise ImportError("eval.separated_stdio was removed; import eval.service.separated_stdio instead")
