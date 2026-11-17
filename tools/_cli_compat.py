"""Preserve script, module and path-loaded forms of organized tool entrypoints."""
from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path


def dispatch(namespace: dict, package: str, name: str) -> None:
    target = Path(__file__).resolve().parent / package / (name + '.py')
    module_name = namespace['__name__']
    if module_name == '__main__':
        runpy.run_path(str(target), run_name='__main__')
    elif module_name in (name, 'tools.' + name) and module_name in sys.modules:
        sys.modules[module_name] = importlib.import_module('tools.' + package + '.' + name)
    else:
        # Path loaders may retain their module object and patch its globals.
        original_file = namespace['__file__']
        try:
            namespace['__file__'] = str(target)
            exec(compile(target.read_bytes(), str(target), 'exec'), namespace)
        finally:
            namespace['__file__'] = original_file
