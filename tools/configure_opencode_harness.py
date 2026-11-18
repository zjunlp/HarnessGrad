#!/usr/bin/env python3
"""Compatibility entrypoint for :mod:`tools.harness_setup.configure_opencode_harness`."""
import sys as _sys
from pathlib import Path as _Path

_root = str(_Path(__file__).resolve().parent.parent)
if _root not in _sys.path:
    _sys.path.insert(0, _root)
from tools._cli_compat import dispatch as _dispatch
_dispatch(globals(), "harness_setup", "configure_opencode_harness")
