"""Storage gates in quality tests must not require this host's private corpus.

These are synthetic path/skip controls, not benchmark measurements. Patch the
shared settings reader so neither inherited environment nor a linked .env can
accidentally make an unconfigured-machine test access real storage.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from harnessgrad import paths

QUALITY = Path(__file__).resolve().parent
CASES = [
    ("test_agentdojo_bridge", "agentdojo", "DATA", "_require_runtime"),
    ("test_spreadsheetbench_official_scorer", "spreadsheetbench", "DATA_ROOT", "bench"),
    ("test_alfworld_official_scorer", "alfworld", "DATA_ROOT", "_require_storage"),
    ("test_container_state", "terminal-bench", "TB_ROOT", "_require_tb"),
]


def _load(name):
    spec = importlib.util.spec_from_file_location(f"portable_{name}", QUALITY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name,directory,attribute,gate", CASES)
def test_unconfigured_integration_gates_skip_before_storage_access(
        monkeypatch, name, directory, attribute, gate):
    monkeypatch.setattr(paths, "_settings", lambda: {})
    module = _load(name)
    assert isinstance(getattr(module, attribute), paths.DatasetPath)
    if name == "test_container_state":
        monkeypatch.setattr(module.container, "available",
                            lambda: pytest.fail("unconfigured storage must gate before Docker"))
    require = getattr(module, gate)
    if gate == "bench":
        require = require.__wrapped__
    with pytest.raises(pytest.skip.Exception, match="Dataset storage is not configured"):
        require()


@pytest.mark.parametrize("name,directory,attribute,gate", CASES)
@pytest.mark.parametrize("per_dataset", [False, True])
def test_quality_paths_follow_shared_and_dataset_overrides(
        monkeypatch, tmp_path, name, directory, attribute, gate, per_dataset):
    shared = tmp_path / "shared"
    override = tmp_path / "override"
    settings = {"HG_DATA_ROOT": str(shared)}
    if per_dataset:
        settings[paths.env_name(directory)] = str(override)
    monkeypatch.setattr(paths, "_settings", lambda: settings)
    module = _load(name)
    assert getattr(module, attribute) == (override if per_dataset else shared / directory)


def test_synthetic_agentdojo_cli_runs_without_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_settings", lambda: {})
    module = _load("test_agentdojo_bridge")
    module.test_cli_has_no_snapshot_path_at_all(tmp_path, monkeypatch)


def test_synthetic_alfworld_reduction_runs_without_storage(monkeypatch):
    monkeypatch.setattr(paths, "_settings", lambda: {})
    module = _load("test_alfworld_official_scorer")
    module.test_the_reduction_takes_the_maximum_not_the_last_value()
