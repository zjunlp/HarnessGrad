"""Immutable runtime projections shared by the two task mount backends.

An entry in setup.files or verify.inputs with ``immutable: true`` is runtime,
not task state. Its ``from`` must name an existing absolute regular directory,
without symlink path components. That exact declared source is bound read-only at
the task's relative ``path``: never copied, staged, cached or duplicated per task.
Unsupported boundaries and invalid sources fail closed; there is no copy fallback.
"""
from __future__ import annotations

from pathlib import Path


class ImmutableRuntimeUnavailable(ValueError):
    """A runtime cannot be projected read-only; the task must be invalid."""


def projections(entries) -> tuple[tuple[Path, str], ...]:
    """Validate immutable contracts without writing anything on the host."""
    result = []
    for entry in entries or ():
        if entry.get("immutable") is not True:
            if entry.get("immutable") not in (None, False):
                raise ImmutableRuntimeUnavailable("immutable must be a boolean")
            continue
        rel = str(entry.get("path") or "")
        path = Path(rel)
        if not rel or path.is_absolute() or path == Path(".") or ".." in path.parts:
            raise ImmutableRuntimeUnavailable(f"invalid immutable runtime path {rel!r}")
        if "from" not in entry or "content" in entry:
            raise ImmutableRuntimeUnavailable("immutable runtime requires only a directory from source")
        source = Path(str(entry["from"]))
        if not source.is_absolute() or ".." in source.parts:
            raise ImmutableRuntimeUnavailable(f"immutable runtime source must be absolute: {source}")
        if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_dir():
            raise ImmutableRuntimeUnavailable(
                f"immutable runtime source must be an existing non-symlink directory: {source}")
        result.append((source, str(path)))
    return tuple(result)


def targets(task_dir: Path, bindings, *, writable_paths=()) -> tuple[tuple[Path, Path], ...]:
    """Revalidate sources and mount targets at launch, rejecting writable aliases.

    Task-created symlinks must not redirect a verifier mount outside the task.
    Identical setup/verify projections are deduplicated; overlapping targets fail
    closed because an overmount could hide a declared runtime or reopen its tree.
    """
    root = task_dir.resolve()
    result = {}
    for source, rel in bindings:
        source, rel = projections([{"path": rel, "from": str(source), "immutable": True}])[0]
        target = root / rel
        current = target
        while current != root:
            if current.is_symlink():
                raise ImmutableRuntimeUnavailable(f"immutable runtime target uses a symlink: {current}")
            current = current.parent
        for writable in (root, *writable_paths):
            writable = Path(writable).resolve()
            if source == writable or writable in source.parents or source in writable.parents:
                raise ImmutableRuntimeUnavailable("immutable runtime source is reachable through writable storage")
            if writable != root and (writable == target or target in writable.parents):
                raise ImmutableRuntimeUnavailable("writable mount would reopen immutable runtime")
        if target in result and result[target] == source:
            continue
        if any(target == other or target in other.parents or other in target.parents
               for other in result):
            raise ImmutableRuntimeUnavailable("overlapping immutable runtime targets")
        result[target] = source
    return tuple((source, target) for target, source in result.items())
