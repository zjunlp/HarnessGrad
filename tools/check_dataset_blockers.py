#!/usr/bin/env python3
"""Compatibility entrypoint for :mod:`tools.registry_ops.check_dataset_blockers`."""
import sys as _sys
from pathlib import Path as _Path

_root = str(_Path(__file__).resolve().parent.parent)
if _root not in _sys.path:
    _sys.path.insert(0, _root)
from tools._cli_compat import dispatch as _dispatch
_dispatch(globals(), "registry_ops", "check_dataset_blockers")
