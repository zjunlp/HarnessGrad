"""Load only the fixed benchmark projection, never an installed upstream framework."""
import importlib
from pathlib import Path
import sys

VENDOR = Path(__file__).resolve().parent / 'vendor'


def activate():
    existing = sys.modules.get('flashrsi')
    if existing is not None and not Path(existing.__file__).resolve().is_relative_to(VENDOR):
        raise RuntimeError('another upstream package is loaded; use a separate preparation process')
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))


def benchmark(name, config):
    from .profiles import PROFILES
    activate()
    module = importlib.import_module('flashrsi.benchmarks.' + name)
    return getattr(module, PROFILES[name]['class'])(config)
