"""Retained command entrypoint; Python imports use the canonical module."""
if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("eval.policy.gaia_preflight", run_name="__main__")
else:
    raise ImportError("eval.gaia_preflight was removed; import eval.policy.gaia_preflight instead")
