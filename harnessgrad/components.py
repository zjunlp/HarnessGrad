"""The harness-component vocabulary, and the attribution of an edit to it.

Two questions that look like one, and are deliberately answered by the same list:

* **which component does a dataset make score-relevant** -- `tools/dataset_classes.py`'s
  `EXERCISES`, the axis that says what a harness must have for a task to move a score;
* **which component did a round's edit touch** -- `editable_surface_components` on the curve
  point, added here.

They are the same ten names because they are the same decomposition of a harness, and keeping
two lists is how a taxonomy rots: the moment one grows, every claim phrased over the other is
silently about a different set. So this module holds the one list and
`tools/dataset_classes.py` re-exports it, which is also the only legal direction --
`tools/` imports `harnessgrad/` and never the reverse.

**Why attribution and not a permission list.** The platform's central design decision is that
a harness is *entirely* editable: `INTERFACE.md` §4.1-4.3 draws the boundary around a
harness's `method/` directory and its `accept` rule (the platform never reads, writes or
imports either), and nowhere else. A declaration that listed the files a method *may* edit
would take that back, and the ability to rewrite its own improvement mechanism is the thing
this platform exists to measure. So `components` is a **map for reading**, never a fence:
a method may edit any file in the tree, and an edit to a path the map does not name is
recorded as unattributed rather than refused.
"""
from __future__ import annotations

import json
from pathlib import Path

#: The ten harness components. Closed, and the single source -- see the module docstring for
#: why there is not a second copy beside the dataset taxonomy.
COMPONENTS = (
    "context",
    "tools",
    "control",
    "memory",
    "verification",
    "recovery",
    "delegation",
    "reliability",
    "cost",
    "safety",
)

#: The manifest key a harness may use to say which of its files implement which component.
DECLARATION_KEY = "components"


class ComponentDeclarationError(ValueError):
    """A `components` block that cannot be read as a declaration."""


def declared_components(root: Path) -> dict[str, list[str]]:
    """The harness's own file-to-component map, or `{}` when it declares none.

    Absent is a legitimate answer and not an error: a harness that predates this key, or one
    whose files do not separate along the components, says nothing and its edits are recorded
    as unattributed. Making the key mandatory would force every harness to invent a mapping
    it does not have, and a mapping nobody believes is worse than a missing one.

    A path may be named by more than one component. That is not a defect to reject -- a
    single-file harness's `agent.py` really does implement context, control and verification
    at once -- so the declaration is a map from components to files and the attribution
    reports every component whose files moved.
    """
    manifest = Path(root) / "harness.json"
    if not manifest.is_file():
        return {}
    try:
        block = (json.loads(manifest.read_text(encoding="utf-8")) or {}).get(DECLARATION_KEY)
    except (OSError, ValueError) as exc:
        raise ComponentDeclarationError(f"harness.json is not readable JSON: {exc}") from exc
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ComponentDeclarationError(
            f"`{DECLARATION_KEY}` must be an object mapping a component to its files")
    out: dict[str, list[str]] = {}
    for name, paths in block.items():
        if name not in COMPONENTS:
            raise ComponentDeclarationError(
                f"`{name}` is not a harness component. The vocabulary is closed "
                f"({', '.join(COMPONENTS)}); add it here first if the harness really needs a "
                f"new one.")
        if not isinstance(paths, list) or not all(isinstance(p, str) and p.strip() for p in paths):
            raise ComponentDeclarationError(f"`{name}` must be a list of relative paths")
        for path in paths:
            rel = Path(path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ComponentDeclarationError(
                    f"`{name}` names {path!r}, which is not inside the harness")
        out[name] = sorted(set(paths))
    return out


def attribute(paths: list[str], declared: dict[str, list[str]]) -> dict:
    """Which components a round's touched paths belong to, and which it cannot place.

    `unattributed` is the honest half and is why this is not a filter: a path the harness did
    not map, or a harness that declared no map at all, still moved, and a reader has to be
    able to tell "the edit was in `control`" from "the edit was somewhere we cannot name".
    Collapsing the second into an empty list would read as "nothing changed".
    """
    touched = sorted({p for p in paths if p})
    if not declared:
        return {"components": [], "unattributed": touched}
    moved = set(touched)
    hit = sorted(name for name, files in declared.items() if moved & set(files))
    claimed = {p for files in declared.values() for p in files}
    return {"components": hit, "unattributed": sorted(moved - claimed)}


__all__ = ["COMPONENTS", "DECLARATION_KEY", "ComponentDeclarationError",
           "declared_components", "attribute"]
