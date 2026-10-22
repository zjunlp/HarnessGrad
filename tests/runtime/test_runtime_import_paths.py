"""Runtime modules live under harnessgrad.runtime without unused flat aliases."""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.mark.parametrize("name", ["events", "validator", "compat"])
def test_runtime_modules_have_no_unused_top_level_shims(name):
    module = importlib.import_module(f"harnessgrad.runtime.{name}")
    assert Path(module.__file__).resolve() == ROOT / "harnessgrad" / "runtime" / f"{name}.py"
    assert importlib.util.find_spec(f"harnessgrad.{name}") is None
