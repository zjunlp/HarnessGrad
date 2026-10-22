"""Pin the dataset catalog order without importing any dataset adapters.

The catalog is a user-visible sequence, even though filesystem enumeration order
is removed by registry.published(). See docs/registry-discovery-order.md.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry  # noqa: E402
from tools import dataset_lint, datasets  # noqa: E402


# Deliberately literal: changing the discovery root must preserve this sequence.
# Membership changes need a reviewed expectation update, not another sorted(...).
EXPECTED_DATASETS = (
    "agentdojo",
    # `aime` (2026-10-16) is the first dataset added since this catalog was pinned, so it is
    # the first membership change this tuple's own comment asks to be reviewed rather than
    # sorted in. It sorts second, between `agentdojo` and `alfworld`.
    "aime",
    "alfworld",
    "api_bank",
    "appworld",
    "bfcl_v3",
    "bird_minidev",
    "chessbench_puzzles",
    "cooperbench",
    "costbench",
    "costbench_dynamic",
    "ds1000",
    "fever",
    "gaia",
    "gsm8k",
    "hotpotqa",
    "jericho",
    "kumo",
    "livecodebench",
    "locomo",
    "memoryagentbench",
    "musique",
    "nestful",
    "personamem_v2",
    "physicsquestions",
    "reasoning_gym",
    "scienceworld",
    "sotuqa",
    "sportsunderstanding",
    "spreadsheetbench",
    "strategyqa",
    "strongreject",
    "tau2_bench",
    "tau3_bench",
    "tau_bench",
    "terminal_bench",
    "triviaqa",
    "twowikimultihopqa",
)


@pytest.fixture
def declared_catalog(monkeypatch):
    """Read literal KIND declarations, with no adapter imports or data loading.

    agentdojo is out of scope: its catalog name is an explicit assumption from
    the existing inventory, and neither its module nor its assets are opened.
    This tests ordering for declared/importable adapters, not runtime readiness.
    """
    def declared_kind(name):
        if name == "agentdojo":
            return registry.KIND_DATASET
        source = ROOT / "data" / "datasets" / f"{name}.py"
        for node in ast.parse(source.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "KIND"
                    for target in node.targets):
                return ast.literal_eval(node.value)
        return registry.KIND_FIXTURE

    monkeypatch.setattr(registry, "kind_of", declared_kind)


@pytest.mark.parametrize("reverse_glob", [False, True])
def test_current_catalog_order_is_checked_in(declared_catalog, monkeypatch, reverse_glob):
    """Both the actual directory listing and its reverse yield the pinned catalog."""
    if reverse_glob:
        original_glob = Path.glob

        def reversed_glob(path, pattern):
            return iter(reversed(list(original_glob(path, pattern))))

        monkeypatch.setattr(Path, "glob", reversed_glob)
    assert registry.published() == EXPECTED_DATASETS


def test_discovery_sorts_before_import_and_filters_without_reordering(monkeypatch):
    """Import failures and fixtures remove entries from the sorted sequence."""
    directory = Path(registry.__file__).resolve().parent
    candidates = [directory / f"{name}.py" for name in (
        "z_order", "registry", "fixture", "broken", "a_order", "__init__")]
    monkeypatch.setattr(Path, "glob", lambda path, pattern: iter(candidates))
    imported = []

    def import_module(name):
        imported.append(name)
        if name == "data.datasets.broken":
            raise ImportError("synthetic unavailable adapter")
        return SimpleNamespace(KIND=(registry.KIND_FIXTURE if name == "data.datasets.fixture"
                                     else registry.KIND_DATASET))

    monkeypatch.setattr(registry, "importlib", SimpleNamespace(import_module=import_module))
    assert registry.published() == ("a_order", "z_order")
    assert imported == ["data.datasets.a_order", "data.datasets.broken", "data.datasets.fixture", "data.datasets.z_order"]


@pytest.mark.parametrize("json_output", [False, True])
def test_download_list_preserves_the_catalog_order(
        declared_catalog, monkeypatch, capsys, json_output):
    """Exercise discovery -> downloads rows -> real --list serialization."""
    entry = {"kind": "file", "name": "synthetic", "version": "v1",
             "size": "0 B", "note": "Synthetic ordering test; nothing is fetched."}
    monkeypatch.setattr(datasets, "importlib", SimpleNamespace(
        import_module=lambda name: SimpleNamespace(downloads=lambda: [entry])))
    assert datasets.main(["--list", *(["--json"] if json_output else [])]) == 0
    output = capsys.readouterr().out
    if json_output:
        names = [row["dataset"] for row in json.loads(output)]
    else:
        names = [line.split(":", 1)[0] for line in output.splitlines()
                 if line and not line.startswith(" ")]
    assert tuple(names) == EXPECTED_DATASETS


def test_lint_json_preserves_the_catalog_order(declared_catalog, monkeypatch, capsys):
    """Stub lint work, keeping discovery, result sequencing and serialization real."""
    monkeypatch.setattr(dataset_lint, "load_schema", lambda: {})
    monkeypatch.setattr(dataset_lint, "lint_dataset",
                        lambda name, schema, **kwargs: dataset_lint.Result(name))
    assert dataset_lint.main(["--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert tuple(row["dataset"] for row in rows) == EXPECTED_DATASETS
