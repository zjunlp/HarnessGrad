"""Source custody and actual selector parity for upstream gepa/_population.py:36-69."""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import types
from pathlib import Path

from native_helpers import P, member, runtime


def test_vendored_sources_match_pinned_inventory():
    root = Path(runtime.__file__).parent
    provenance = json.loads((root / "PROVENANCE.json").read_text())
    assert provenance["commit"] == "e9c7667945f959d62da27204649a82f94d1b6705"
    for name, digest in provenance["files"].items():
        assert (
            hashlib.sha256((root / "vendor" / name).read_bytes()).hexdigest() == digest
        )
    assert "Apache License" in (root / "vendor/LICENSE.Apache-2.0").read_text()


def test_gepa_frontier_and_prune_match_native_source_over_random_populations():
    # Execute an isolated, source-pinned copy with only its type-only Snapshot
    # import replaced. This neither imports upstream nor trusts our own selector.
    source = Path("/tmp/native-e9c7667945f9/upstream/algorithms/gepa/_population.py")
    # The actual source is copied into the worktree for portable tests.
    source = Path(runtime.__file__).parent / "vendor/gepa/_population.py.reference"
    # The vendored file's only dependency is a type-only import of `Snapshot`; replace it by
    # pattern, so this test does not have to spell the source package's name to run its bytes.
    text = re.sub(
        r"^from \w+\.core import Snapshot$", "Snapshot = object",
        source.read_text(), flags=re.MULTILINE)
    mod = types.ModuleType("_native_gepa_population_test")
    sys.modules[mod.__name__] = mod
    exec(compile(text, str(source), "exec"), mod.__dict__)
    rng = random.Random(32)
    for _ in range(100):
        rows = [
            member(i, 0, {str(t): rng.choice([0, 0.5, 1]) for t in range(4)})
            for i in range(rng.randint(1, 8))
        ]
        rows = [member(m.round, sum(m.per_task.values()) / 4, m.per_task) for m in rows]
        expected = mod.prune(mod.frontier(rows), rows)
        assert P.pareto_front(rows) == expected
