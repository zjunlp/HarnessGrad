"""Scoped readiness facts, not run admission or a cache of dataset integrity.

Legacy ``present=False`` can mean absent bytes, corrupt digests or missing runtimes.
Never infer a reason from that boolean or from free-form exception/detail text.
Callers take a fresh snapshot per operation; normalization performs no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass


STATUSES = frozenset({"unchecked", "config_missing", "data_missing", "invalid",
                      "unavailable", "refused", "ready"})


@dataclass(frozen=True)
class Readiness:
    """``ready`` is meaningful only together with the scope that was checked."""

    status: str
    scope: str
    detail: str = ""

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown readiness status: {self.status!r}")
        if not self.scope:
            raise ValueError("readiness needs an explicit scope")

    def as_dict(self) -> dict:
        return {"status": self.status, "scope": self.scope, "detail": self.detail}


def normalize_provision_status(snapshot: dict) -> Readiness:
    """Normalize one already-collected legacy status, preserving its limited claim.

    Optional explicit ``status``/``scope`` declarations permit incremental adapter
    migration. ``present`` remains mandatory and must agree with a ready status.
    No adapter is called here, and the caller retains the original snapshot.
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("present"), bool):
        raise ValueError("provision_status() must return a dict with boolean `present`")
    status = snapshot.get("status", "ready" if snapshot["present"] else "unavailable")
    if not isinstance(status, str) or status not in STATUSES or status == "unchecked":
        raise ValueError("provision_status() declares an invalid status")
    if snapshot["present"] != (status == "ready"):
        raise ValueError("provision_status() status contradicts `present`")
    scope = snapshot.get("scope", "adapter-provision-status")
    if not isinstance(scope, str) or not scope:
        raise ValueError("provision_status() scope must be a non-empty string")
    return Readiness(status, scope, str(snapshot.get("detail") or ""))
