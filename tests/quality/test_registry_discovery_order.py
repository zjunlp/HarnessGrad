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
    # `authored_example_fix_bug` (2026-11-02) 是控制台"起草"流程产出的示例场景：它是一份
    # 真的适配器（KIND = dataset），所以它出现在目录里是对的。成员变化照这个元组的规矩
    # 做一次评审再加入，而不是回头去过滤。
    "authored_example_fix_bug",
    # **The upstream transfer (2026-11-02) added twenty reviewed adapters.** Each has a
    # section in `docs/dataset-reviews/transfer-review.md` (source pin, metric, what is
    # honest missing) and a `KIND = "dataset"` declaration, so the reviewed-membership
    # rule above is satisfied; they are listed in sorted order like every other entry.
    "bbeh", "bfcl_v3", "bird_minidev",
    "browsecomp", "chessbench_puzzles", "cooperbench",
    "costbench", "costbench_dynamic", "deepsearchqa",
    "deepswe", "ds1000", "fever",
    "gaia", "gsm8k", "harvey_lab",
    "hle", "hotpotqa", "jericho",
    "jobbench", "kumo", "livecodebench",
    "livemathbench", "locomo", "math",
    "math500", "memoryagentbench", "musique",
    "nestful", "nq", "officeqa_pro",
    "personamem_v2", "physicsquestions", "reasoning_gym",
    "scienceworld", "searchqa", "sotuqa",
    "sportsunderstanding", "spreadsheetbench", "spreadsheetbench2",
    "strategyqa", "strongreject", "supergpqa",
    "swe_bench", "swe_bench_pro", "tau2_bench",
    "tau3_bench", "tau_bench", "terminal_bench",
    "travelplanner", "triviaqa", "twowikimultihopqa",
    "widesearch", "workbench",
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


def test_discovery_sorts_declarations_and_filters_without_runtime_imports(monkeypatch):
    """Unreadable declarations and fixtures are excluded; missing runtimes are harmless."""
    directory = Path(registry.__file__).resolve().parent
    candidates = [directory / f"{name}.py" for name in (
        "z_order", "registry", "fixture", "broken", "a_order", "__init__")]
    monkeypatch.setattr(Path, "glob", lambda path, pattern: iter(candidates))
    read = []
    original_read = Path.read_bytes

    def read_bytes(path):
        if path.stem not in {"a_order", "broken", "fixture", "z_order"}:
            return original_read(path)
        read.append(path.stem)
        if path.stem == "broken":
            return b"KIND ="
        kind = registry.KIND_FIXTURE if path.stem == "fixture" else registry.KIND_DATASET
        return f'KIND = "{kind}"\nraise ImportError("runtime unavailable")\n'.encode()

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(registry, "importlib", SimpleNamespace(
        import_module=lambda name: pytest.fail("catalog discovery imported a runtime")))
    assert registry.published() == ("a_order", "z_order")
    assert read == ["a_order", "broken", "fixture", "z_order"]


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
