"""Trusted phase limits and private agent package projections."""
from __future__ import annotations

import math
from pathlib import Path

from eval.observe.tracing import _materialize


def admit_native_network(env: dict, manifest: dict, agent_backend: str) -> None:
    """Admit only network policies the native mount/transport path can honour.

    Live offline agents use the platform's AF_UNIX model route inside unshare-net;
    role and backend no longer determine whether that network policy is possible.
    """
    network = env.get("network")
    if network not in (None, "none"):
        raise ValueError(f"unsupported native sandbox network policy: {network!r}")


def phase_timeout(*limits, default: float = 3600) -> float:
    """Honor every explicit bound; an omitted caller limit is only a fallback."""
    values = []
    for limit in limits:
        if limit is None:
            continue
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) \
                or not math.isfinite(limit) or limit <= 0:
            raise ValueError(f"invalid phase timeout: {limit!r}")
        values.append(limit)
    return min(values) if values else default


def stage_agent_runtime(spec: dict, root: Path) -> tuple[Path, Path]:
    """Build an allowlisted package outside agent-writable task storage."""
    package = Path(spec["package_dir"])
    if not package.is_absolute() or not package.is_dir() or package.is_symlink():
        raise ValueError("agent_runtime.package_dir must be an existing absolute package directory")
    files = spec.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("agent_runtime.files must be a nonempty allowlist")
    if root.exists() and any(root.iterdir()):
        raise ValueError("agent_runtime projection directory must be empty")
    names = set()
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("agent_runtime files need explicit relative paths")
        path = Path(entry["path"])
        if not path.parts or path.is_absolute() or ".." in path.parts \
                or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            raise ValueError("agent_runtime file path is unsafe or contains bytecode")
        if path in names:
            raise ValueError("agent_runtime file paths must be unique")
        names.add(path)
        if ("content" in entry) == ("from" in entry):
            raise ValueError("agent_runtime files need exactly one content or from source")
        if "from" in entry:
            source = Path(entry["from"])
            if not source.is_absolute() or source.is_symlink() or not source.is_file():
                raise ValueError("agent_runtime sources must be absolute regular files")
    root.mkdir(parents=True, exist_ok=True)
    _materialize(root, files, Path(__file__).resolve().parents[2])
    if not (root / "__init__.py").is_file():
        raise ValueError("agent_runtime requires __init__.py")
    return root, package
