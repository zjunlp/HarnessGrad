"""Read method declarations without importing or running method code.

Method metadata describes the existing experiment-oriented ports. It does not
say that a method can run in a different workflow, nor does it prove readiness.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MethodDescription:
    """One method directory and its declared, unverified metadata.

    ``metadata`` is None when a declaration is missing or invalid. ``error``
    distinguishes that condition from a valid empty declaration. An absent
    ``requires`` field must not be interpreted as having no requirements.
    """

    name: str
    directory: Path
    entrypoint: Path | None
    metadata: dict[str, Any] | None
    error: str | None

    @property
    def declared_requirements(self) -> dict[str, Any] | None:
        if self.metadata is None:
            return None
        value = self.metadata.get("requires")
        return dict(value) if isinstance(value, dict) else None


def describe_method(directory: str | Path) -> MethodDescription:
    """Read the declaration in a caller-selected method directory, without importing it."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"method directory does not exist: {directory}")
    entry = directory / "run.py"
    entrypoint = entry if entry.is_file() else None
    path = directory / "method.json"
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("method.json must contain an object")
    except (OSError, ValueError) as exc:
        return MethodDescription(directory.name, directory, entrypoint, None, str(exc))
    return MethodDescription(directory.name, directory, entrypoint, metadata, None)


def discover_methods(methods_root: str | Path) -> tuple[MethodDescription, ...]:
    """Inspect visible method directories with a declaration or Python entrypoint.

    Missing roots are reported, not confused with an empty catalog. Malformed
    metadata remains visible as an error rather than being treated as readiness.
    This is a read-only catalog and does not discover user-supplied commands.
    """
    root = Path(methods_root)
    if not root.is_dir():
        raise ValueError(f"methods directory does not exist: {root}")
    result = []
    for directory in sorted(root.iterdir()):
        if directory.name.startswith(("_", ".")) or not directory.is_dir():
            continue
        if not (directory / "method.json").is_file() and not (directory / "run.py").is_file():
            continue
        result.append(describe_method(directory))
    return tuple(result)
