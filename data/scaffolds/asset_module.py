"""Load one trusted adapter-owned helper shared by related adapters."""
from functools import lru_cache
import importlib.util
from pathlib import Path


@lru_cache(maxsize=None)
def load(path: Path):
    spec = importlib.util.spec_from_file_location('_harnessgrad_dataset_asset', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
