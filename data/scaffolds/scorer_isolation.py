"""Scorer bundle declaration vocabulary, shared by registry and execution.

This is metadata only; the executor must independently record which class ran.
"""
from __future__ import annotations

CLASSES = frozenset({"pristine-offline", "snapshot-service", "actor-workdir", "in-process", "unknown"})


def declared_class(spec: dict | None) -> str:
    bundle = (spec or {}).get("scorer_bundle")
    if bundle is None:
        return "unknown"
    if (not isinstance(bundle, dict) or not isinstance(bundle.get("isolation_class"), str)
            or bundle["isolation_class"] not in CLASSES):
        raise ValueError("scorer_bundle requires a supported isolation_class")
    return bundle["isolation_class"]
