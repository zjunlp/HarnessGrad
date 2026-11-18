"""Scoped readiness facts shared by workflow-specific checks."""
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
