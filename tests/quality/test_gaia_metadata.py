"""Metadata queries work before gated GAIA data is provisioned."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def unprovisioned_gaia(tmp_path, monkeypatch):
    module = importlib.import_module('data.datasets.gaia')
    monkeypatch.setattr(module, 'DATASET_ROOT', tmp_path)
    monkeypatch.setattr(module, '_RAW', None)
    monkeypatch.setattr(module, '_BUILT', {})
    return module


@pytest.mark.parametrize('name', ['REFUSED', '__path__', 'unknown_runtime_field'])
def test_unknown_metadata_defaults_without_reading_a_pool(unprovisioned_gaia, name):
    """A catalog's absent-attribute fallback cannot depend on benchmark data."""
    missing = object()
    assert getattr(unprovisioned_gaia, name, missing) is missing


@pytest.mark.parametrize('name', ['ENV', 'SPLIT', 'SETUP', 'VERIFY'])
def test_runtime_attribute_still_refuses_an_unprovisioned_pool(unprovisioned_gaia, name):
    with pytest.raises(ValueError, match='gaia: no pool at'):
        getattr(unprovisioned_gaia, name)
