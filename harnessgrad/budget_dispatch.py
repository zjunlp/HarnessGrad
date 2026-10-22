"""A small, offline, trusted-caller provider dispatch boundary.

This module deliberately has no provider or HTTP dependency.  The caller supplies an
already tokenized prompt count and a callback factory.  A reservation is committed to a
locked ledger before the factory is called.  The ledger is the authority for this boundary;
trace counters and subject supplied metadata are never read as budget evidence.

The production driver and network mediation are intentionally outside this module.  See
``docs/trusted_budget_dispatch.md`` for the current scope.
"""

from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import inspect
import json
import math
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BudgetDispatchError(RuntimeError):
    """Base class for a refused dispatch or invalid ledger operation."""


class ProfileValidationError(BudgetDispatchError, ValueError):
    """The profile is missing, malformed, or contains unsafe limits."""


class LedgerError(BudgetDispatchError):
    """The persistent ledger cannot be trusted."""


class LedgerCorrupt(LedgerError):
    """The ledger is not valid JSON or does not have the expected shape."""


class LedgerTampered(LedgerError):
    """The ledger's integrity or immutable profile identity does not match."""


class StaleRequest(LedgerError):
    """A reconciliation or retry referred to an unknown request id."""


class BudgetExceeded(BudgetDispatchError):
    """A reservation would cross a run, role, or wall-clock limit."""

    def __init__(self, counter: str, current: Any = None, limit: Any = None):
        self.counter, self.current, self.limit = counter, current, limit
        super().__init__(f"budget exceeded: {counter} (current={current!r}, limit={limit!r})")


class UsageValidationError(BudgetDispatchError, ValueError):
    """Provider usage is not a non-negative integer accounting record."""


class BudgetViolation(BudgetDispatchError):
    """Reported usage exceeds the reservation; accounting fails closed."""


class ProviderExecutionUnknown(BudgetDispatchError):
    """The provider boundary raised and execution cannot be proved absent."""


def _jsonable(value: Any, path: str = "profile") -> Any:
    """Validate and deep-copy JSON data, rejecting NaN, infinities, and exotic values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProfileValidationError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, f"{path}[{i}]") for i, v in enumerate(value)]
    raise ProfileValidationError(f"{path} contains a non-JSON value")


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError("profile is not canonical JSON") from exc


def profile_hash(profile: Mapping[str, Any]) -> str:
    """Return the stable SHA-256 identity of a validated profile."""
    clean = _jsonable(profile)
    if not isinstance(clean, dict):
        raise ProfileValidationError("profile must be an object")
    return hashlib.sha256(_canonical(clean)).hexdigest()


_ALIASES = {
    "wall": ("wall_seconds", "wall_s", "wall_clock_s", "wall", "wall_limit_s",
             "max_wall_seconds"),
    "requests": ("max_requests", "requests", "request", "request_limit"),
    "calls": ("max_calls", "calls", "call", "logical_calls", "call_limit"),
    "retries": ("max_retries", "retries", "retry", "retry_limit"),
    "prompt": ("max_prompt_tokens", "prompt_tokens", "prompt", "prompt_token_limit"),
    "output": ("max_output_tokens", "output_tokens", "output", "output_token_limit"),
    "total": ("max_total_tokens", "global_total_tokens", "total_tokens",
               "globaltotal", "global_total", "global_total_limit"),
    "max_context": ("max_context", "max_context_tokens", "context_tokens", "context"),
    "request_timeout": ("request_timeout_seconds", "request_timeout_s",
                         "timeout_seconds", "timeout_s"),
}


def _first(mapping: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _number(value: Any, name: str, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileValidationError(f"{name} must be a non-negative finite number")
    if not math.isfinite(float(value)) or value < 0:
        raise ProfileValidationError(f"{name} must be a non-negative finite number")
    if integer and int(value) != value:
        raise ProfileValidationError(f"{name} must be a non-negative integer")
    return int(value) if integer else value


def _normal_limits(raw: Mapping[str, Any], *, where: str) -> dict[str, int | float]:
    out: dict[str, int | float] = {}
    for name, aliases in _ALIASES.items():
        value = _first(raw, aliases)
        if value is not None:
            out[name] = _number(value, f"{where}.{name}", integer=name != "wall" and name != "request_timeout")  # type: ignore[assignment]
    return out


@dataclass(frozen=True)
class BudgetProfile:
    """Validated, immutable profile data used by :class:`BudgetDispatcher`."""

    data: dict[str, Any]
    digest: str
    global_limits: dict[str, int | float]
    role_limits: dict[str, dict[str, int | float]]

    @classmethod
    def from_value(cls, profile: Mapping[str, Any] | BudgetProfile) -> BudgetProfile:
        if isinstance(profile, cls):
            return profile
        if not isinstance(profile, Mapping):
            raise ProfileValidationError("profile must be an object")
        clean = _jsonable(profile)
        if not isinstance(clean, dict):  # pragma: no cover - guarded above
            raise ProfileValidationError("profile must be an object")
        version = clean.get("profile_version", 1)
        if version != 1:
            raise ProfileValidationError("unsupported profile_version")
        limits = clean.get("limits")
        if limits is not None and not isinstance(limits, Mapping):
            raise ProfileValidationError("profile limits must be an object")
        # Accept both a conventional ``limits.global`` shape and flat limits.  The
        # canonical input remains intact, so changing any profile field changes its hash.
        flat = dict(limits or {})
        global_raw: dict[str, Any] = {}
        if isinstance(clean.get("global"), Mapping):
            global_raw.update(clean["global"])
        if isinstance(clean.get("global_limits"), Mapping):
            global_raw.update(clean["global_limits"])
        if isinstance(flat.get("global"), Mapping):
            global_raw.update(flat["global"])
        if isinstance(clean.get("arm"), Mapping):
            global_raw.update(clean["arm"])
        if isinstance(clean.get("arm_limits"), Mapping):
            global_raw.update(clean["arm_limits"])
        if isinstance(clean.get("run"), Mapping):
            global_raw.update(clean["run"])
        if isinstance(clean.get("run_limits"), Mapping):
            global_raw.update(clean["run_limits"])
        if isinstance(flat.get("arm"), Mapping):
            global_raw.update(flat["arm"])
        if isinstance(flat.get("run"), Mapping):
            global_raw.update(flat["run"])
        global_raw.update({k: v for k, v in flat.items() if k != "global"})
        global_raw.update({k: v for k, v in clean.items() if k in sum(_ALIASES.values(), ())})
        global_limits = _normal_limits(global_raw, where="global")
        roles_raw = clean.get("roles", clean.get("role_limits", flat.get("roles", {})))
        if roles_raw is None:
            roles_raw = {}
        if not isinstance(roles_raw, Mapping):
            raise ProfileValidationError("profile roles must be an object")
        role_limits: dict[str, dict[str, int | float]] = {}
        for role, raw in roles_raw.items():
            if not isinstance(role, str) or not role.strip():
                raise ProfileValidationError("role names must be non-empty strings")
            if not isinstance(raw, Mapping):
                raise ProfileValidationError(f"limits for role {role!r} must be an object")
            nested = raw.get("limits") if isinstance(raw.get("limits"), Mapping) else raw
            role_limits[role] = _normal_limits(nested, where=f"role {role!r}")
        digest = hashlib.sha256(_canonical(clean)).hexdigest()
        return cls(copy.deepcopy(clean), digest, global_limits, role_limits)

    @classmethod
    def from_json(cls, source: str | os.PathLike[str]) -> BudgetProfile:
        path = Path(source)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfileValidationError(f"cannot read profile {path}") from exc
        return cls.from_value(value)

    @property
    def max_context(self) -> int | None:
        value = self.global_limits.get("max_context")
        return int(value) if value is not None else None

    def limits_for(self, role: str) -> dict[str, int | float]:
        if not isinstance(role, str) or not role.strip():
            raise ProfileValidationError("role must be a non-empty string")
        result = dict(self.global_limits)
        result.update(self.role_limits.get(role, {}))
        return result


@dataclass(frozen=True)
class DispatchRequest:
    request_id: str
    attempt_id: str
    role: str
    prompt_tokens: int
    max_output_tokens: int
    max_tokens: int
    timeout: float | None
    deadline: float | None
    retry_of: str | None
    payload: Any


@dataclass(frozen=True)
class DispatchResult:
    """Optional structured result returned by ``dispatch_with_result``."""

    response: Any
    request_id: str
    attempt_id: str
    usage: dict[str, int] | None


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


class _Ledger:
    """Atomic JSON ledger with one lock shared by processes and threads."""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self.thread_lock = _thread_lock(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        with self.thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                state = self._read()
                try:
                    yield state
                except Exception:
                    # A fail-closed path (for example usage larger than its reservation)
                    # still has to persist its violation and unknown reservation.
                    self._write(state)
                    raise
                else:
                    self._write(state)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def read(self) -> dict[str, Any]:
        with self.thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
                try:
                    return self._read()
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            raise LedgerCorrupt(f"ledger does not exist: {self.path}")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LedgerCorrupt(f"corrupt ledger: {self.path}") from exc
        if not isinstance(value, dict) or value.get("ledger_version") != 1:
            raise LedgerCorrupt("unsupported ledger shape")
        claimed = value.get("ledger_hash")
        body = {k: v for k, v in value.items() if k != "ledger_hash"}
        actual = hashlib.sha256(_canonical(body)).hexdigest()
        if not isinstance(claimed, str) or not secrets.compare_digest(claimed, actual):
            raise LedgerTampered("ledger integrity hash mismatch")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        body = {k: v for k, v in value.items() if k != "ledger_hash"}
        value["ledger_hash"] = hashlib.sha256(_canonical(body)).hexdigest()
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(value, out, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
            try:
                dfd = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        finally:
            try:
                os.unlink(name)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UsageValidationError(f"{name} must be a non-negative integer")
    return value


class BudgetDispatcher:
    """Trusted, file-backed reservation and dispatch boundary."""

    def __init__(self, profile: Mapping[str, Any] | BudgetProfile,
                 ledger_path: str | os.PathLike[str], *, run_id: str = "run",
                 clock: Callable[[], float] = time.time):
        self.profile = BudgetProfile.from_value(profile)
        self.ledger = _Ledger(Path(ledger_path))
        self.run_id = run_id
        if not isinstance(run_id, str) or not run_id:
            raise ProfileValidationError("run_id must be a non-empty string")
        self.clock = clock
        self.last_request_id: str | None = None
        self.last_attempt_id: str | None = None
        self._ensure_ledger()

    def _ensure_ledger(self) -> None:
        with self.ledger.thread_lock, self.ledger.lock_path.open("a+", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    if self.ledger.path.exists():
                        state = self.ledger._read()
                        if state.get("run_id") != self.run_id:
                            raise LedgerTampered("ledger belongs to a different run")
                        if state.get("profile_hash") != self.profile.digest:
                            raise LedgerTampered("profile hash changed or profile was reused")
                        if state.get("profile") != self.profile.data:
                            raise LedgerTampered("profile contents changed")
                    else:
                        now = float(self.clock())
                        wall = self.profile.global_limits.get("wall")
                        state = {
                            "ledger_version": 1, "run_id": self.run_id,
                            "profile_hash": self.profile.digest,
                            "profile": self.profile.data,
                            "started_at": now,
                            "deadline": now + float(wall) if wall is not None else None,
                            "sequence": 0, "requests": {},
                            "reserved": {"prompt_tokens": 0, "output_tokens": 0,
                                         "total_tokens": 0},
                            "spent": {"prompt_tokens": 0, "output_tokens": 0,
                                       "total_tokens": 0},
                            "counts": {"requests": 0, "calls": 0, "retries": 0},
                            "roles": {}, "unknown_requests": [], "violations": [],
                            "metadata": self.metadata(),
                        }
                        self.ledger._write(state)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def metadata(self) -> dict[str, Any]:
        return {
            "supported_scope": "trusted_dispatch_only",
            "arbitrary_harness_network_enforcement": False,
            "production_driver_network_mediation": "PENDING",
            "profile_hash": self.profile.digest,
            "run_id": self.run_id,
        }

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.ledger.read())

    def _limits(self, role: str) -> dict[str, int | float]:
        return self.profile.limits_for(role)

    @staticmethod
    def _request_key(state: Mapping[str, Any], identifier: str) -> str | None:
        if identifier in state.get("requests", {}):
            return identifier
        for key, record in state.get("requests", {}).items():
            if record.get("attempt_id") == identifier:
                return key
        return None

    def _fail_if(self, state: dict[str, Any], role: str, prompt: int, output: int,
                 retry: bool) -> tuple[float | None, dict[str, int | float]]:
        now = float(self.clock())
        deadline = state.get("deadline")
        if deadline is not None and now >= float(deadline):
            raise BudgetExceeded("wall", now - float(state["started_at"]),
                                 float(deadline) - float(state["started_at"]))
        limits = self._limits(role)
        counts = state["counts"]
        for key, amount in (("requests", 1), ("calls", 1), ("retries", int(retry))):
            limit = limits.get(key)
            if limit is not None and counts[key] + amount > limit:
                raise BudgetExceeded(key, counts[key], limit)
        reserved = state["reserved"]
        for key, amount in (("prompt_tokens", prompt), ("output_tokens", output),
                            ("total_tokens", prompt + output)):
            cap_name = {"prompt_tokens": "prompt", "output_tokens": "output",
                        "total_tokens": "total"}[key]
            # The global aggregate is already in limits. Role limits are merged into
            # the same mapping and therefore apply to every role independently below.
            limit = limits.get(cap_name)
            if limit is not None:
                role_state = state["roles"].get(role, {})
                prior = role_state.get(f"spent_{key}", 0) + role_state.get(key, 0)
                if prior + amount > limit:
                    raise BudgetExceeded(f"role.{role}.{key}", prior, limit)
            global_limit = self.profile.global_limits.get(cap_name)
            prior_global = state["spent"][key] + reserved[key]
            if global_limit is not None and prior_global + amount > global_limit:
                raise BudgetExceeded(f"global.{key}", prior_global, global_limit)
        context = limits.get("max_context", self.profile.max_context)
        if context is not None and prompt + output > context:
            raise BudgetExceeded("max_context", prompt + output, context)
        timeout = None
        request_limit = limits.get("request_timeout")
        if request_limit is not None:
            timeout = float(request_limit)
        if deadline is not None:
            remaining = float(deadline) - now
            timeout = remaining if timeout is None else min(timeout, remaining)
            if timeout <= 0:
                raise BudgetExceeded("wall", 0, timeout)
        return timeout

    def _reserve(self, role: str, prompt: int, output: int,
                 retry_of: str | None) -> DispatchRequest:
        if not isinstance(role, str) or not role.strip():
            raise ProfileValidationError("role must be a non-empty string")
        prompt = _integer(prompt, "prompt_tokens")
        output = _integer(output, "max_output_tokens")
        with self.ledger.transaction() as state:
            if state.get("profile_hash") != self.profile.digest or state.get("profile") != self.profile.data:
                raise LedgerTampered("immutable profile identity mismatch")
            unknown = state.get("unknown_requests", [])
            if unknown and (prompt or output):
                raise BudgetExceeded("unknown_usage", unknown, "reconciliation required")
            retry = retry_of is not None
            if retry:
                prior_key = (self._request_key(state, retry_of)
                             if isinstance(retry_of, str) else None)
                if prior_key is None:
                    raise StaleRequest(f"unknown retry_of request id: {retry_of!r}")
                prior = state["requests"][prior_key]
                if prior.get("status") != "settled":
                    raise BudgetExceeded("retry_of", retry_of, "settled request")
            timeout = self._fail_if(state, role, prompt, output, retry)
            state["sequence"] += 1
            sequence = state["sequence"]
            request_id = f"req-{sequence:08d}"
            attempt_id = f"attempt-{sequence:08d}"
            total = prompt + output
            record = {
                "request_id": request_id, "attempt_id": attempt_id,
                "role": role, "retry_of": retry_of,
                "prompt_reserved": prompt, "output_reserved": output,
                "total_reserved": total, "status": "reserved",
                "created_at": float(self.clock()),
            }
            state["requests"][request_id] = record
            state["counts"]["requests"] += 1
            state["counts"]["calls"] += 1
            state["counts"]["retries"] += int(retry)
            role_state = state["roles"].setdefault(role, {"requests": 0, "calls": 0,
                "retries": 0, "prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                "spent_prompt_tokens": 0, "spent_output_tokens": 0,
                "spent_total_tokens": 0})
            role_state["requests"] += 1
            role_state["calls"] += 1
            role_state["retries"] += int(retry)
            for key, amount in (("prompt_tokens", prompt), ("output_tokens", output),
                                ("total_tokens", total)):
                state["reserved"][key] += amount
                role_state[key] += amount
            self.last_request_id, self.last_attempt_id = request_id, attempt_id
            return DispatchRequest(request_id, attempt_id, role, prompt, output, output,
                                   timeout, state.get("deadline"), retry_of, None)

    @staticmethod
    def _call_factory(factory: Callable[..., Any], request: DispatchRequest,
                      payload: Any) -> Any:
        """Call a factory exactly once, accepting common offline stub signatures."""
        try:
            sig = inspect.signature(factory)
            params = list(sig.parameters.values())
            positional = [p for p in params if p.kind in
                          (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
            has_var = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
            names = {p.name for p in params}
        except (TypeError, ValueError):
            positional, has_var, has_var_kw, names = [], True, True, set()
        # It is convenient for tiny offline tests to pass a provider callback directly.
        # Its bounded keyword parameters identify that form without ever invoking it here.
        if ("max_tokens" in names or "timeout" in names or "request_timeout" in names or
                (has_var_kw and positional and positional[0].name.lower() in
                 {"payload", "body", "message"})):
            return factory
        if has_var or len(positional) >= 2:
            first = positional[0].name.lower() if positional else ""
            return factory(request, payload) if "request" in first else factory(payload, request)
        if not positional:
            return factory()
        name = positional[0].name.lower()
        return factory(payload if "payload" in name or name in {"body", "message"} else request)

    @staticmethod
    def _invoke(provider: Any, payload: Any, request: DispatchRequest) -> Any:
        target = provider
        if not callable(target):
            for name in ("invoke", "complete", "call", "dispatch"):
                candidate = getattr(provider, name, None)
                if callable(candidate):
                    target = candidate
                    break
        if not callable(target):
            raise TypeError("callback factory did not return a callable provider")
        try:
            sig = inspect.signature(target)
            params = sig.parameters
            kwargs: dict[str, Any] = {}
            if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()) or "max_tokens" in params:
                kwargs["max_tokens"] = request.max_tokens
            if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()) or "timeout" in params:
                kwargs["timeout"] = request.timeout
            elif "request_timeout" in params:
                kwargs["request_timeout"] = request.timeout
        except (TypeError, ValueError):
            # A non-introspectable offline callable still gets the bounded request as a
            # keyword pair.  Do not catch errors raised by the invocation itself.
            return target(payload, max_tokens=request.max_tokens, timeout=request.timeout)
        return target(payload, **kwargs)

    @staticmethod
    def _extract_usage(response: Any) -> tuple[Any, Mapping[str, Any] | None]:
        if isinstance(response, tuple) and len(response) == 2:
            usage = response[1]
            if isinstance(usage, Mapping):
                return response[0], usage
            dump = getattr(usage, "model_dump", None)
            if callable(dump):
                value = dump()
                if isinstance(value, Mapping):
                    return response[0], value
        if isinstance(response, Mapping) and isinstance(response.get("usage"), Mapping):
            return response, response["usage"]
        usage = getattr(response, "usage", None)
        if isinstance(usage, Mapping):
            return response, usage
        dump = getattr(usage, "model_dump", None)
        if callable(dump):
            value = dump()
            if isinstance(value, Mapping):
                return response, value
        if hasattr(usage, "prompt_tokens") and hasattr(usage, "completion_tokens"):
            return response, {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        return response, None

    def reconcile(self, request_id: str, usage: Mapping[str, Any] | None,
                  *, provider_did_not_execute: bool = False) -> None:
        if not isinstance(request_id, str):
            raise StaleRequest("request_id must be a string")
        with self.ledger.transaction() as state:
            request_key = self._request_key(state, request_id)
            record = state["requests"].get(request_key) if request_key else None
            if record is None:
                raise StaleRequest(f"unknown request id: {request_id!r}")
            request_id = request_key
            if record["status"] == "settled":
                raise StaleRequest(f"request already reconciled: {request_id!r}")
            if provider_did_not_execute:
                if usage is not None:
                    raise UsageValidationError("no-execution proof cannot include usage")
                self._release(state, record)
                record["status"] = "abandoned"
                if request_id in state["unknown_requests"]:
                    state["unknown_requests"].remove(request_id)
                return
            if usage is None:
                if request_id not in state["unknown_requests"]:
                    state["unknown_requests"].append(request_id)
                record["status"] = "unknown"
                return
            if not isinstance(usage, Mapping):
                if request_id not in state["unknown_requests"]:
                    state["unknown_requests"].append(request_id)
                record["status"] = "unknown"
                state["violations"].append({"request_id": request_id,
                    "reason": "usage is not an object", "at": float(self.clock())})
                raise UsageValidationError("usage must be an object")
            normalized: dict[str, int] = {}
            try:
                for key, value in usage.items():
                    if key in {"prompt_tokens", "input_tokens"}:
                        normalized["prompt_tokens"] = _integer(value, key)
                    elif key in {"completion_tokens", "output_tokens"}:
                        normalized["output_tokens"] = _integer(value, key)
                    elif key == "total_tokens":
                        normalized[key] = _integer(value, key)
                    elif key not in {
                        "cached_tokens", "cache_read_tokens", "reasoning_tokens",
                        "completion_tokens_details", "prompt_tokens_details",
                    }:
                        raise UsageValidationError(f"unsupported usage field: {key}")
            except UsageValidationError as exc:
                if request_id not in state["unknown_requests"]:
                    state["unknown_requests"].append(request_id)
                record["status"] = "unknown"
                state["violations"].append({"request_id": request_id,
                    "reason": str(exc), "at": float(self.clock())})
                raise
            if not {"prompt_tokens", "output_tokens"}.issubset(normalized):
                if request_id not in state["unknown_requests"]:
                    state["unknown_requests"].append(request_id)
                record["status"] = "unknown"
                state["violations"].append({"request_id": request_id,
                    "reason": "usage missing prompt or completion fields",
                    "at": float(self.clock())})
                raise UsageValidationError("usage must contain prompt_tokens and completion_tokens")
            actual_prompt = normalized["prompt_tokens"]
            actual_output = normalized["output_tokens"]
            actual_total = normalized.get("total_tokens", actual_prompt + actual_output)
            if actual_total != actual_prompt + actual_output:
                self._violate(state, record, "total_tokens does not equal prompt plus output")
            if actual_prompt > record["prompt_reserved"] or actual_output > record["output_reserved"] or actual_total > record["total_reserved"]:
                self._violate(state, record, "usage exceeded reservation")
            self._release(state, record)
            for key, value in (("prompt_tokens", actual_prompt), ("output_tokens", actual_output),
                               ("total_tokens", actual_total)):
                state["spent"][key] += value
                state["roles"][record["role"]][f"spent_{key}"] += value
            record.update({"status": "settled", "usage": normalized})
            if request_id in state["unknown_requests"]:
                state["unknown_requests"].remove(request_id)

    def _violate(self, state: dict[str, Any], record: dict[str, Any], reason: str) -> None:
        state["violations"].append({"request_id": record["request_id"], "reason": reason,
                                    "at": float(self.clock())})
        record["status"] = "violation"
        if record["request_id"] not in state["unknown_requests"]:
            state["unknown_requests"].append(record["request_id"])
        raise BudgetViolation(reason)

    @staticmethod
    def _release(state: dict[str, Any], record: dict[str, Any]) -> None:
        role_state = state["roles"][record["role"]]
        for key, amount in (("prompt_tokens", record["prompt_reserved"]),
                            ("output_tokens", record["output_reserved"]),
                            ("total_tokens", record["total_reserved"])):
            state["reserved"][key] -= amount
            role_state[key] -= amount

    def dispatch_with_result(self, callback_factory: Callable[..., Any], payload: Any,
                             role: str, prompt_tokens: int, max_output_tokens: int,
                             retry_of: str | None = None) -> DispatchResult:
        request = self._reserve(role, prompt_tokens, max_output_tokens, retry_of)
        request = DispatchRequest(**{**request.__dict__, "payload": payload})
        try:
            provider = self._call_factory(callback_factory, request, payload)
            response = self._invoke(provider, payload, request)
        except Exception as exc:
            self.reconcile(request.request_id, None)
            raise ProviderExecutionUnknown(
                f"provider execution unknown for {request.request_id}") from exc
        value, usage = self._extract_usage(response)
        if usage is not None:
            self.reconcile(request.request_id, usage)
            normalized = self.snapshot()["requests"][request.request_id].get("usage")
        else:
            self.reconcile(request.request_id, None)
            normalized = None
        return DispatchResult(value, request.request_id, request.attempt_id, normalized)

    def dispatch(self, callback_factory: Callable[..., Any], payload: Any, role: str,
                 prompt_tokens: int, max_output_tokens: int,
                 retry_of: str | None = None) -> Any:
        """Reserve then invoke an offline callback, returning its response value."""
        return self.dispatch_with_result(callback_factory, payload, role, prompt_tokens,
                                         max_output_tokens, retry_of).response


# A module-level entry point for callers that already have a configured dispatcher.  The
# keyword keeps the required six-argument interface clear while avoiding any hidden default
# profile or network client.
def dispatch(callback_factory: Callable[..., Any], payload: Any, role: str,
             prompt_tokens: int, max_output_tokens: int,
             retry_of: str | None = None, *, dispatcher: BudgetDispatcher | None = None) -> Any:
    if dispatcher is None:
        raise ProfileValidationError("dispatch requires a configured BudgetDispatcher")
    return dispatcher.dispatch(callback_factory, payload, role, prompt_tokens,
                               max_output_tokens, retry_of)


TrustedBudgetDispatcher = BudgetDispatcher

__all__ = [
    "BudgetDispatchError",
    "BudgetDispatcher",
    "BudgetExceeded",
    "BudgetProfile",
    "BudgetViolation",
    "DispatchRequest",
    "DispatchResult",
    "LedgerCorrupt",
    "LedgerError",
    "LedgerTampered",
    "ProfileValidationError",
    "ProviderExecutionUnknown",
    "StaleRequest",
    "TrustedBudgetDispatcher",
    "UsageValidationError",
    "dispatch",
    "profile_hash",
]
