"""Retained command entrypoint; Python imports use the canonical module."""
if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("eval.admission.scorer_bundle", run_name="__main__")
else:
    raise ImportError("eval.scorer_bundle was removed; import eval.admission.scorer_bundle instead")
