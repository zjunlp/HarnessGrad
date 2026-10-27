"""Manifest runtime declarations and source copying; stdlib-only checkpoint helpers."""
from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath

BUN_GENERATED = frozenset({
    ".git", "node_modules", ".state", "__pycache__",
    ".opencode-state", "_harnessgrad",
})


def runtime_spec(root: Path) -> dict | None:
    """Read an explicit runtime contract, never infer capabilities from identity."""
    path = Path(root) / "harness.json"
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid harness manifest") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Harness manifest must be an object")
    spec = manifest.get("runtime")
    if spec is None:
        return None
    if not isinstance(spec, dict) or spec.get("kind") != "bun":
        raise ValueError("runtime must declare supported kind 'bun'")
    if set(spec) != {"kind", "source", "entrypoint", "package"}:
        raise ValueError("Bun runtime requires kind, source, entrypoint and package")
    for name in ("source", "entrypoint"):
        value = spec[name]
        if (not isinstance(value, str) or not value or "\\" in value or ":" in value
                or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts
                or any(p in BUN_GENERATED for p in PurePosixPath(value).parts)
                or str(PurePosixPath(value)) != value):
            raise ValueError(f"Bun runtime {name} must be a normalized owned relative path")
    package = spec["package"]
    if (not isinstance(package, str) or not package or package.startswith("-")
            or any(c.isspace() for c in package) or any(c in package for c in "*[]!\\")):
        raise ValueError("Bun runtime package must name one workspace package")
    return dict(spec)


def has_bun_runtime(root: Path) -> bool:
    return runtime_spec(root) is not None


def copy_harness(source, target, **kwargs):
    """Keep declared Bun source separate from dependencies and generated state."""
    if has_bun_runtime(Path(source)):
        previous = kwargs.get("ignore")
        kwargs["ignore"] = lambda directory, names: (
            set(names) & BUN_GENERATED
        ) | (set(previous(directory, names)) if previous else set())
        kwargs["symlinks"] = True
    return shutil.copytree(source, target, **kwargs)
