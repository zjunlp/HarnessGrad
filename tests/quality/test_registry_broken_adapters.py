"""Omitted source declarations are reported without requiring runtime installation.

The source catalog retains metadata for unprovisioned adapters. Unreadable declarations
are omitted with a reason, while data/dependency failures remain load-time diagnostics.
The download CLI keeps its JSON payload separate from diagnostic text on stderr.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry  # noqa: E402


def test_a_broken_declaration_is_dropped_from_published_and_named(monkeypatch):
    original = Path.read_bytes

    def read_bytes(path):
        if path == ROOT / "data/datasets/gsm8k.py":
            return b'KIND = "dataset"\ninvalid syntax !\n'
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    names = registry.published()
    broken = registry.broken_adapters()

    assert "gsm8k" not in names
    assert broken.get("gsm8k", "").startswith("SyntaxError"), broken
    # The rest of the catalogue is unaffected, so this is one row, not an empty list.
    assert "agentdojo" in names, names


def test_every_declared_adapter_is_published_or_named_as_broken():
    """Every source declaration is published or reported as unreadable."""
    declared = {path.stem for path in (ROOT / "data/datasets").glob("*.py")
                if path.stem != "__init__"}
    published = set(registry.published())
    broken = registry.broken_adapters()
    assert published <= declared, f"published names with no declaration: {published - declared}"
    assert set(broken) <= declared, f"broken names with no declaration: {set(broken) - declared}"
    assert published | set(broken) == declared, (
        "declared adapters that are neither published nor named as broken: "
        f"{sorted(declared - published - set(broken))}")
    assert not (published & set(broken)), "an adapter is both published and reported broken"
    print(f"published {len(published)} of {len(declared)} declared, {len(broken)} broken here")


def test_checkout_has_all_reviewed_adapter_declarations():
    published = registry.published()
    broken = registry.broken_adapters()
    assert not broken, (
        "these adapter declarations cannot be read, so the catalogue is missing them: "
        + "; ".join(f"{name} ({reason})" for name, reason in sorted(broken.items())))
    declared = len([path for path in (ROOT / "data/datasets").glob("*.py")
                    if path.stem != "__init__"])
    assert len(published) == declared, (
        f"expected every declared adapter ({declared}) to be published, got {len(published)}")


def test_missing_runtime_is_reported_at_load_without_shortening_catalog(monkeypatch, tmp_path):
    from harnessgrad.dataset_runtime import DatasetRuntimeUnavailable

    # Select only new empty task-owned storage, never a developer's prepared runtime.
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setenv("HG_DATA_GSM8K", str(tmp_path / "datasets" / "gsm8k"))
    runtime_root = tmp_path / "runtime-store"
    monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", str(runtime_root))
    declared = len([path for path in (ROOT / "data/datasets").glob("*.py")
                    if path.stem != "__init__"])
    assert len(registry.published()) == declared
    assert registry.broken_adapters() == {}
    with pytest.raises(DatasetRuntimeUnavailable) as failure:
        registry.load("gsm8k")
    assert str(runtime_root) in str(failure.value)
    assert "ready.json" in str(failure.value)
    assert "prepare_dataset.py --dataset gsm8k" in str(failure.value)
    assert len(registry.published()) == declared
    assert not runtime_root.exists(), "read-only loading must not provision a runtime"


def test_nonprovider_import_failure_remains_visible_at_load(monkeypatch):
    def unavailable(name):
        raise ModuleNotFoundError("synthetic unavailable runtime")

    monkeypatch.setattr(registry, "importlib", SimpleNamespace(import_module=unavailable))
    assert "authored_example_fix_bug" in registry.published()
    with pytest.raises(ModuleNotFoundError, match="synthetic unavailable runtime"):
        registry.load("authored_example_fix_bug")


def test_download_json_keeps_catalog_diagnostics_on_stderr(monkeypatch, capsys):
    from tools import datasets
    monkeypatch.setattr(datasets, "datasets_with_downloads", lambda **kwargs: [("example", [])])
    monkeypatch.setattr(registry, "broken_adapters", lambda: {"broken": "SyntaxError: invalid declaration"})
    assert datasets.main(["--list", "--json"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == [{"dataset": "example", "downloads": []}]
    assert "broken: SyntaxError" in output.err
