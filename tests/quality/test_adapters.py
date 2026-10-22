"""The deleted `legacy/adapters/` archive must not come back as an import.

`legacy/adapters/` held replay adapters for six methods (`ahe`, `sica`, `dgm`, `rrsi`,
`hyperagents`, `metaharness`) whose uniform entrypoint was `legacy/adapters/context.py`.
Its own README said "historical record, not a supported route", `methods/<name>/run.py`
became the supported route, and 8c938596 deleted the directory -- but not this file, so
its nine tests stayed red on main for four days: two of them assert on a README that no
longer exists and seven import `legacy.adapters.<name>`.

The one thing that is still worth checking is the boundary the deletion was meant to
create: **no production module may import that path**, so a copy of it cannot reappear as
a dependency. That is the whole file now. The archive itself is recoverable from history
(8c938596) if it is ever wanted back.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_production_module_imports_legacy_adapters():
    violations = []
    # Skip the archive and the tests themselves, plus every *generated* tree. The
    # platform root of a working checkout also holds `runs/` -- historical run states
    # that are untracked, not ours to lint, and not always parseable (a recorded
    # agent.py is a snapshot of model output, not a module that has to compile).
    # Walking them made this test crash on a real checkout while passing in a clean
    # worktree, which is the worst version of a false signal.
    generated = {"runs", "current_harness", "web", "reports", "__pycache__"}
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {"legacy", "tests"} | generated or any(
                part.startswith(".") or part == "__pycache__"
                for part in relative.parts):
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(relative))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            if any(name == prefix or name.startswith(prefix + ".")
                   for name in names for prefix in ("adapters", "legacy.adapters")):
                violations.append(f"{relative}:{node.lineno}")
    assert not violations, "production imports historical adapters: " + ", ".join(violations)
