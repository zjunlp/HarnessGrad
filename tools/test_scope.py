"""Which tests belong to a dataset, and which belong to the platform.

The problem this solves
-----------------------
The quality suite is one flat directory of ~140 files. Running `pytest tests/quality`
runs all of them, so a contributor working on one adapter pays for every other
adapter's tests -- including files that spend twenty minutes in real container and
replay work. That is the wrong shape: a test for AppWorld is *about* AppWorld, and
someone editing the GSM8K scorer has no reason to run it.

The rule
--------
Three kinds of file, decided by evidence rather than by a hand-kept list:

* **dataset-scoped** -- the dataset name appears in the *filename*
  (`test_appworld_reporting.py`). The filename is the authoritative signal: it is
  what the author chose to say the file is about, and it does not drift when the
  body happens to use another dataset as a fixture.
* **registry-wide** -- a small, explicitly listed set that verifies the *contract
  across all datasets* (`test_dataset_controls.py` checks all 29). These are not
  per-dataset work; they belong to "I changed the dataset layer".
* **core** -- everything else: platform tests. A few of them use one dataset as an
  example fixture (terminal_bench is a common one). They are *not* dataset-scoped,
  because breaking them is a platform regression, not an adapter regression.

A body mention alone therefore does **not** make a file dataset-scoped. That rule
matters: a naive "does the text contain the name" match attributed
`test_import_direction.py` to five datasets and `test_split_source.py` to five
more, which would have made every dataset selection pull in platform tests.

`EXTRA` is the short, reviewed exception list: files that are genuinely about one
dataset but were not named after it (`test_lcb740_identity.py` is the LiveCodeBench
row-740 check). Each entry is a decision someone made, not a guess.

Reading this module is enough to answer "what do I run for this dataset?" without
loading any dataset: it only looks at file names and a fixed set of names.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Cross-dataset contract tests. They verify the dataset *layer*, so they run when
#: that layer changes, not when one adapter is edited. Each checks many datasets and
#: is correspondingly slow.
REGISTRY_WIDE = (
    "test_dataset_controls.py",
    "test_dataset_blockers.py",
    "test_dataset_classes.py",
    "test_dataset_lint.py",
    "test_qa_dataset_contract.py",
)

#: Dataset-specific files whose names do not carry the dataset name. Reviewed by
#: hand; a guess here would silently mis-scope a file.
EXTRA = {
    "test_bfcl_selected.py": "bfcl_v3",
    "test_bfcl_ast_source_contract.py": "bfcl_v3",
    "test_lcb740_identity.py": "livecodebench",
    "test_chessbench_selected_control.py": "chessbench_puzzles",
    "test_stepwise_transport.py": "chessbench_puzzles",
    "test_ujson_pypi_codex226.py": "twowikimultihopqa",
}


def published(registry_module=None) -> list[str]:
    """The dataset names to scope against.

    `data.scaffolds.registry.published()` imports the adapters. This module is used by the
    test runner, which must be able to print a plan without loading datasets, so a
    caller may pass a pre-computed list instead.
    """
    if registry_module is not None:
        return sorted(registry_module.published())
    import sys
    sys.path.insert(0, str(ROOT))
    from data.scaffolds import registry
    return sorted(registry.published())


def test_files() -> list[Path]:
    return sorted((ROOT / "tests").rglob("test_*.py"))


def scope_map(names: list[str]) -> dict[str, set[str]]:
    """dataset -> set of test file names, plus the two non-dataset keys."""
    out: dict[str, set[str]] = {"core": set(), "registry": set(),
                               **{n: set() for n in names}}
    out["registry"].update(REGISTRY_WIDE)
    for path in test_files():
        if path.name in REGISTRY_WIDE:
            continue
        matched = {n for n in names if n in path.name}
        if not matched and path.name in EXTRA:
            matched = {EXTRA[path.name]}
        if matched:
            for n in matched:
                out[n].add(path.name)
        else:
            out["core"].add(path.name)
    return out


def select(names: list[str], datasets: list[str] | None = None, *,
           include_core: bool = True, include_registry: bool = False) -> list[str]:
    """The file names to run for `datasets`.

    Defaults are deliberate: core is included (a platform regression still matters
    while you work on an adapter) and registry-wide is not (it checks all 29 and is
    the slowest thing in the suite; run it when you touch the dataset layer).
    """
    mapping = scope_map(names)
    chosen: set[str] = set()
    if include_core:
        chosen |= mapping["core"]
    if include_registry:
        chosen |= mapping["registry"]
    for d in datasets or []:
        if d not in mapping:
            raise KeyError(f"unknown dataset {d!r}")
        chosen |= mapping[d]
    return sorted(chosen)
