"""Read-only descriptions of declared harness surfaces.

A manifest describes file attribution and execution environments, not verified
runtime capabilities or edit permissions. Inspection never starts a harness.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .components import _parse_component_block, attribute


@dataclass(frozen=True)
class HarnessDescription:
    """Declared identity and components of one harness; not runtime evidence."""

    root: Path
    name: str | None
    version: str | None
    entrypoint: str | None
    backend: str | None
    env_kinds: tuple[str, ...]
    components: tuple[tuple[str, tuple[str, ...]], ...]

    def attribute_changes(self, paths: list[str]) -> dict:
        """Classify changed paths without treating declarations as edit fences."""
        return attribute(paths, {name: list(files) for name, files in self.components})


def describe_harness(root: str | Path) -> HarnessDescription:
    """Describe declarations in a local manifest; do not infer observed behavior.

    This is an inspection helper, not the existing harness admission/validation API.
    The caller controls which directory is read. Missing or malformed manifests fail
    rather than yielding a fabricated capability list.
    """
    root = Path(root)
    manifest = root / "harness.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read harness manifest {manifest}: {exc}") from exc
    return describe_harness_manifest(root, value)


def describe_harness_manifest(root: str | Path, value: object) -> HarnessDescription:
    """Describe an already-read manifest using the same declaration rules."""
    root = Path(root)
    manifest = root / "harness.json"
    if not isinstance(value, dict):
        raise ValueError(f"harness manifest {manifest} must contain an object")
    fields = {}
    for name in ("name", "version", "entrypoint", "backend"):
        item = value.get(name)
        if item is not None and not isinstance(item, str):
            raise ValueError(f"{name} must be a string when declared")
        fields[name] = item
    kinds = value.get("env_kinds", [])
    if not isinstance(kinds, list) or not all(isinstance(item, str) for item in kinds):
        raise ValueError("env_kinds must be a list of strings when declared")
    components = _parse_component_block(value.get("components"))
    return HarnessDescription(
        root=root, env_kinds=tuple(kinds),
        components=tuple((name, tuple(files)) for name, files in sorted(components.items())),
        **fields,
    )
