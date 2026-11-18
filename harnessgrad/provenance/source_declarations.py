"""Read literal declarations from source without importing or executing it."""
from __future__ import annotations

import ast


def literal_assignments(source: str | bytes, names: set[str], *, allow_chained: bool = False) -> dict:
    """Return uniquely declared top-level literals; reject missing or duplicate names.

    Single-name assignments are the default. A caller may explicitly permit
    chained assignments when the source contract declares the same value twice.
    This reads source text only and does not authenticate its origin or execution.
    """
    values = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or (not allow_chained and len(node.targets) != 1):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name) and target.id in names]
        if not targets:
            continue
        value = ast.literal_eval(node.value)
        for name in targets:
            if name in values:
                raise ValueError('duplicate literal declaration')
            values[name] = value
    if set(values) != names:
        raise ValueError('missing literal declaration')
    return values
