"""The run record: what a point and a run *are*, and how to check one.

`harnessgrad/records.py` writes the record; this package declares its shape and answers
the reader's question -- is this run a measurement? -- without executing anything.
"""
from harnessgrad.store.schema import certify, check_point, check_run_meta, read_curve

__all__ = ["certify", "check_point", "check_run_meta", "read_curve"]
