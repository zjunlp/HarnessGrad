#!/usr/bin/env python3
"""Retained command entrypoint; Python imports use dataset_maintenance."""
import sys as _sys
from pathlib import Path as _Path

if __name__ == '__main__':
    import runpy as _runpy
    _root = str(_Path(__file__).resolve().parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    _runpy.run_module('tools.dataset_maintenance.new_dataset', run_name='__main__')
else:
    raise ImportError('tools.new_dataset was removed; import tools.dataset_maintenance.new_dataset instead')
