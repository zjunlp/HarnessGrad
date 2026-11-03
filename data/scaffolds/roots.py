"""Dataset root diagnostics using the common explicit storage configuration.

There is no legacy-path, checkout-sibling or per-user directory fallback.
Adapters use harnessgrad.paths directly; these diagnostics share its exact rules.
"""
from __future__ import annotations

import pathlib


def env_name(name: str) -> str:
    from harnessgrad.paths import env_name as canonical_name
    return canonical_name(name)


def dataset_root(name: str):
    from harnessgrad.paths import dataset_root as configured_root
    return configured_root(name)


def describe(name: str, resolved=None) -> dict:
    """Report the configured path, or an actionable unconfigured status."""
    from harnessgrad.paths import DataRootNotConfigured, _settings
    values = _settings()
    variable = env_name(name)
    source = (variable if values.get(variable, "").strip() else
              "HG_DATA_ROOT" if values.get("HG_DATA_ROOT", "").strip() else "unconfigured")
    try:
        if resolved is None:
            root = pathlib.Path(dataset_root(name))
        else:
            root = pathlib.Path(resolved).expanduser().resolve()
            if source == "unconfigured" or root != pathlib.Path(dataset_root(name)):
                source = "explicit"
    except DataRootNotConfigured as exc:
        return {"name": name, "root": None, "source": "unconfigured",
                "exists": False, "error": str(exc)}
    return {"name": name, "root": str(root), "source": source, "exists": root.exists()}
