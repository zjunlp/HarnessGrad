"""Driver-owned binding for the trusted budget dispatcher.

The dispatcher in :mod:`harnessgrad.budget_dispatch` is intentionally a small
trusted-caller primitive.  This module is the platform boundary that binds one
validated profile to one run and arm, and exposes the primitive to a provider
factory that the platform owns.  It contains no provider SDK and performs no
network I/O.

The binding is optional.  A caller with no profile gets the legacy path and no
budget accounting.  Once a profile is supplied, initialization is fail closed:
the profile, ledger, run identity and arm identity must all be consistent before
the caller can construct a provider.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harnessgrad.budget_dispatch import (
    BudgetDispatcher,
    BudgetDispatchError,
    BudgetProfile,
    DispatchResult,
    profile_hash,
)


class BudgetConfigurationError(BudgetDispatchError):
    """A configured profile cannot be bound to the requested run."""


_UNIT_COUNTERS = (
    "candidates",
    "rounds",
    "trials",
    "jobs",
)


def load_profile(source: Mapping[str, Any] | str | os.PathLike[str] | None) -> BudgetProfile | None:
    """Load a profile from a mapping, JSON text, or a JSON file.

    ``None`` is the explicit legacy mode.  A missing file, malformed JSON, or
    invalid profile raises ``BudgetConfigurationError`` before any provider can
    be constructed.
    """
    if source is None or source == "":
        return None
    try:
        if isinstance(source, Mapping):
            value = source
        elif isinstance(source, os.PathLike):
            value = json.loads(Path(source).read_text(encoding="utf-8"))
        elif isinstance(source, str):
            candidate = Path(source).expanduser()
            if candidate.is_file():
                value = json.loads(candidate.read_text(encoding="utf-8"))
            else:
                value = json.loads(source)
        else:
            raise TypeError("profile source must be a mapping, JSON text, path, or None")
        return BudgetProfile.from_value(value)
    except BudgetConfigurationError:
        raise
    except Exception as exc:
        raise BudgetConfigurationError(f"invalid budget profile: {exc}") from exc


def _identity_part(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BudgetConfigurationError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass
class BudgetBinding:
    """One immutable profile/ledger/run/arm binding.

    The dispatcher's file-backed ledger remains the authority for provider
    reservations and token usage.  Unit counters are kept separately here so
    candidates, rounds, trials and jobs cannot be confused with provider calls.
    """

    profile: BudgetProfile
    ledger_path: Path
    run_id: str
    arm_id: str
    dispatcher: BudgetDispatcher
    _units: dict[str, int] = field(default_factory=lambda: {k: 0 for k in _UNIT_COUNTERS})

    @classmethod
    def create(cls, profile: Mapping[str, Any] | BudgetProfile | str | os.PathLike[str],
               ledger_path: str | os.PathLike[str], *, run_id: str,
               arm_id: str = "agent") -> BudgetBinding:
        parsed = load_profile(profile) if not isinstance(profile, BudgetProfile) else profile
        if parsed is None:
            raise BudgetConfigurationError("a configured binding requires a profile")
        run = _identity_part(run_id, "run_id")
        arm = _identity_part(arm_id, "arm_id")
        path = Path(ledger_path).expanduser()
        if path.exists() and path.is_dir():
            raise BudgetConfigurationError(f"ledger path is a directory: {path}")
        # Include the arm in the dispatcher identity.  A shared ledger can never
        # silently be reused by a different arm, while the profile hash still
        # prevents edits or profile reuse within that identity.
        dispatcher_run_id = f"{run}::{arm}"
        try:
            dispatcher = BudgetDispatcher(parsed, path, run_id=dispatcher_run_id)
        except Exception as exc:
            raise BudgetConfigurationError(
                f"cannot initialize budget ledger {path}: {exc}") from exc
        return cls(parsed, path, run, arm, dispatcher)

    @property
    def profile_hash(self) -> str:
        """The immutable identity of the validated profile."""
        return self.profile.digest

    @property
    def enabled(self) -> bool:
        return True

    def record(self, counter: str, amount: int = 1) -> None:
        """Record a non-provider unit without conflating it with a call.

        Provider token and attempt counters come only from the dispatcher ledger.
        These four orchestration counters are deliberately separate and are not
        inferred from traces or subject reports.
        """
        if counter not in _UNIT_COUNTERS:
            raise ValueError(f"unknown budget unit counter: {counter}")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("unit counter amount must be a non-negative integer")
        limits = self.profile.data.get("limits")
        if isinstance(limits, Mapping) and isinstance(limits.get("global"), Mapping):
            limits = limits.get("global")
        role_limits = self.profile.data.get("roles", {}).get(self.arm_id, {})
        role_limits = role_limits.get("limits", role_limits) if isinstance(role_limits, Mapping) else {}
        limit = None
        if isinstance(role_limits, Mapping) and counter in role_limits:
            limit = role_limits[counter]
        elif isinstance(limits, Mapping):
            limit = limits.get(counter)
        current = self._units[counter]
        if isinstance(limit, (int, float)) and current + amount > limit:
            from harnessgrad.budget_dispatch import BudgetExceeded
            raise BudgetExceeded(f"{self.arm_id}.{counter}", current, limit)
        self._units[counter] = current + amount

    def dispatch_with_result(self, callback_factory: Callable[..., Any], payload: Any,
                             role: str, prompt_tokens: int, max_output_tokens: int,
                             retry_of: str | None = None) -> DispatchResult:
        """Reserve, construct and invoke a provider through Task43."""
        return self.dispatcher.dispatch_with_result(
            callback_factory, payload, role, prompt_tokens, max_output_tokens, retry_of)

    def dispatch(self, callback_factory: Callable[..., Any], payload: Any, role: str,
                 prompt_tokens: int, max_output_tokens: int,
                 retry_of: str | None = None) -> Any:
        """Trusted provider call; reservation precedes factory construction."""
        return self.dispatch_with_result(
            callback_factory, payload, role, prompt_tokens, max_output_tokens, retry_of).response

    def counters(self) -> dict[str, int | None]:
        state = self.dispatcher.snapshot()
        counts = state.get("counts", {})
        spent = state.get("spent", {})
        unknown = bool(state.get("unknown_requests"))
        return {
            "calls": int(counts.get("calls", 0)),
            "attempts": int(counts.get("requests", 0)),
            "retries": int(counts.get("retries", 0)),
            "prompt_tokens": None if unknown else int(spent.get("prompt_tokens", 0)),
            "output_tokens": None if unknown else int(spent.get("output_tokens", 0)),
            **{key: self._units[key] for key in _UNIT_COUNTERS},
        }

    def metadata(self) -> dict[str, Any]:
        """A secret-free, run-record representation of the binding."""
        return {
            "configured": True,
            "profile_hash": self.profile_hash,
            "ledger_path": str(self.ledger_path),
            "run_id": self.run_id,
            "arm_id": self.arm_id,
            "dispatcher_run_id": self.dispatcher.run_id,
            "scope": self.dispatcher.metadata(),
            "counters": self.counters(),
        }


@dataclass
class ProviderBoundary:
    """Optional platform-owned gateway with an explicit legacy path."""

    binding: BudgetBinding | None = None

    @property
    def configured(self) -> bool:
        return self.binding is not None

    def dispatch(self, callback_factory: Callable[..., Any], payload: Any, role: str,
                 prompt_tokens: int, max_output_tokens: int,
                 retry_of: str | None = None) -> Any:
        if self.binding is not None:
            return self.binding.dispatch(callback_factory, payload, role,
                                         prompt_tokens, max_output_tokens, retry_of)
        # Legacy callers retain their direct provider construction path.  This
        # function deliberately does not impose a profile or provider arguments.
        provider = callback_factory(payload)
        return provider(payload) if callable(provider) else provider

    def metadata(self) -> dict[str, Any] | None:
        return self.binding.metadata() if self.binding is not None else None


def bind_budget(profile: Mapping[str, Any] | BudgetProfile | str | os.PathLike[str] | None,
                ledger_path: str | os.PathLike[str] | None, *, run_id: str,
                arm_id: str = "agent") -> BudgetBinding | None:
    """Return a trusted binding or ``None`` for legacy/no-profile execution."""
    if profile is None or profile == "":
        return None
    if ledger_path is None or str(ledger_path).strip() == "":
        raise BudgetConfigurationError("a budget profile requires an explicit ledger path")
    return BudgetBinding.create(profile, ledger_path, run_id=run_id, arm_id=arm_id)


__all__ = [
    "BudgetBinding",
    "BudgetConfigurationError",
    "ProviderBoundary",
    "bind_budget",
    "load_profile",
    "profile_hash",
]
