"""Retained command entrypoint; Python imports use the canonical module."""
if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("eval.containers.harness_runtime", run_name="__main__")
else:
    raise ImportError("eval.harness_runtime was removed; import eval.containers.harness_runtime instead")
