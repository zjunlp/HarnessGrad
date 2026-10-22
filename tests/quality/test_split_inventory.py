"""`docs/dataset-splits.md` is generated from the adapters, so it cannot drift silently.

Why a test and not a comment: the document is a table of 40 rows, each of which is a claim
about what a dataset's `SPLIT` and `OFFICIAL_EVAL` are. A row that stops matching its
adapter is a reader being told the wrong division -- the same failure the document exists to
prevent -- and nothing else in the suite would notice. `tools/split_inventory.py --check`
renders every row from the modules and compares, so the fix is always
`--write`, never a hand edit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_split_inventory_matches_every_adapter():
    proc = subprocess.run([sys.executable, "tools/split_inventory.py", "--check"],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_every_dataset_is_in_the_table_exactly_once():
    """Published, once each -- a missing row is a dataset a reader cannot look up."""
    from data.scaffolds import registry
    import tools.split_inventory as inventory

    rows = [inventory._row(name) for name in registry.published()]
    assert [row["dataset"] for row in rows] == list(registry.published())
    assert len({row["dataset"] for row in rows}) == len(rows)
