"""Image declaration regressions; no Docker, benchmark execution or model calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=[("gaia", "HG_GAIA_IMAGE"), ("tau_bench", "HG_TAU_IMAGE")])
def adapter(request, monkeypatch):
    name, environment_key = request.param
    monkeypatch.syspath_prepend(str(ROOT))

    def fresh_import():
        # A separate namespace avoids cached declarations and leaking an override
        # into other tests that import the canonical adapter module.
        spec = importlib.util.spec_from_file_location(
            f"image_pin_test_{name}", ROOT / "data" / "datasets" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return environment_key, fresh_import


def test_adapter_image_default_is_digest_pinned(adapter, monkeypatch):
    environment_key, fresh_import = adapter
    monkeypatch.delenv(environment_key, raising=False)
    module = fresh_import()
    assert "@sha256:" in module.IMAGE
    assert re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", module.IMAGE)
    assert module.DEFAULT_ENV["image"] == module.IMAGE


@pytest.mark.parametrize("override", ["operator/custom:override", ""])
def test_adapter_image_environment_override_wins(adapter, monkeypatch, override):
    environment_key, fresh_import = adapter
    monkeypatch.setenv(environment_key, override)
    module = fresh_import()
    assert module.IMAGE == override
    assert module.DEFAULT_ENV["image"] == override
