"""Reuse native inventories only during declaration planning, never checker launch."""
from copy import deepcopy
import os

from data.scaffolds.declarations import once
from . import native


def selected_native():
    manifest = os.environ.get('HG_SPREADSHEET_NATIVE_MANIFEST')
    return deepcopy(once(('spreadsheet-native', manifest),
                         lambda: native.snapshot_native_python(native.require_runtime())))
