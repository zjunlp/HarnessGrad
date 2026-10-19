"""Environment -> dataclasses, and the one snapshot a process reads.

Three entry points:

* `load(mapping)` builds a `Config` from a mapping. The default is `os.environ`, but
  the argument is explicit so a test can ask "what would this environment configure?"
  without touching the process environment.
* `current()` is the process snapshot: built once and reused, so every reader in a run
  sees the same value of every variable. That is the point -- the configuration *is*
  the environment, and reads scattered across time can observe a variable mutate
  mid-run. `reset()` drops the snapshot, which is what a test that configures the
  platform after some earlier call needs.
* `dump()` and `describe()` are the two projections of the same declaration: the
  effective configuration as a JSON-serialisable dict (what a run records), and the
  documentation of every field (what `--help` prints).

`current()` also watches the declared variables and rebuilds when one of them changes
underneath it. In a run that does not mutate its own environment -- every run that is
not a test, and every read after `driver.py` has folded `.env` in -- the snapshot is
built exactly once. The watch exists because Python's test runner configures the
platform with `monkeypatch.setenv`, and a snapshot that ignored that would make a test
observe a value the test did not set.
"""
from __future__ import annotations

from dataclasses import asdict, fields
import os
from typing import Any, Mapping

from harnessgrad.config.areas import (
    AgentConfig, JudgeConfig, Config, EgressConfig, EnvironmentConfig, ImproverConfig,
    MethodConfig, RunConfig, RuntimeConfig, SandboxConfig, StorageConfig,
)


# ------------------------------------------------------------------ parsing ---

def _parse(raw: str | None, default: Any, kind: str) -> Any:
    """One raw environment value, as the point-of-use read would have produced it."""
    if kind == "int":
        # `int(os.environ.get("HG_X", "0"))`: an empty or non-numeric value raises,
        # exactly as it did at the point of use.
        return default if raw is None else int(raw)
    if kind == "int_opt":
        # `int(os.environ.get("HG_X", "0") or "0")`: empty falls back to the default.
        return default if raw is None or raw == "" else int(raw)
    if kind == "float":
        return default if raw is None else float(raw)
    if kind == "opt":
        # `os.environ.get("HG_X") or None`: unset and empty both mean "not configured".
        return None if not raw else raw
    if kind == "opt_raw":
        # Unset is None; set-but-empty stays "", because one caller passes it to Path().
        return None if raw is None else raw
    # "str": `os.environ.get("HG_X", "d")`, where "" is itself a value.
    return default if raw is None else raw


def _load_area(cls: type, env: Mapping[str, str]) -> Any:
    values = {}
    for spec in fields(cls):
        meta = spec.metadata
        values[spec.name] = _parse(env.get(meta["env"]), spec.default, meta["kind"])
    return cls(**values)


def load(env: Mapping[str, str] | None = None) -> Config:
    """Build the configuration from an environment mapping (default `os.environ`).

    Explicit, not magic: every field's variable name lives in its dataclass metadata,
    so this is a loop over a declaration rather than a naming convention. Nothing is
    read from the process environment unless the caller asks for it.
    """
    source = os.environ if env is None else env
    return Config(
        agent=_load_area(AgentConfig, source),
        judge=_load_area(JudgeConfig, source),
        method=_load_area(MethodConfig, source),
        improver=_load_area(ImproverConfig, source),
        environment=_load_area(EnvironmentConfig, source),
        sandbox=_load_area(SandboxConfig, source),
        egress=_load_area(EgressConfig, source),
        runtime=_load_area(RuntimeConfig, source),
        storage=_load_area(StorageConfig, source),
    )


# ---------------------------------------------------------------- snapshot ---

#: The root areas in the order `dump()` and `describe()` present them, and the class
#: behind each name (used by `describe()` to reach the defaults without building a
#: whole `Config`).
_AREA_CLASSES: dict[str, type] = {
    "agent": AgentConfig,
    "judge": JudgeConfig,
    "method": MethodConfig,
    "improver": ImproverConfig,
    "environment": EnvironmentConfig,
    "sandbox": SandboxConfig,
    "egress": EgressConfig,
    "runtime": RuntimeConfig,
    "storage": StorageConfig,
}
_AREAS = tuple(_AREA_CLASSES)

#: Every declared variable, so `current()` can tell whether its snapshot is still the
#: environment it was taken from. Sorted and computed once from the declarations.
ENV_NAMES: tuple[str, ...] = tuple(sorted(
    spec.metadata["env"] for cls in _AREA_CLASSES.values() for spec in fields(cls)))

_CURRENT: Config | None = None
_RAW: tuple[str | None, ...] | None = None


def _raw(env: Mapping[str, str]) -> tuple[str | None, ...]:
    return tuple(env.get(name) for name in ENV_NAMES)


def current() -> Config:
    """The process-wide configuration snapshot.

    Built on first call and reused, because the configuration *is* the environment: one
    snapshot is strictly more honest than reads scattered across time, which is how a
    run came to observe two values of one variable. It is rebuilt only when a declared
    variable's raw value has changed since the snapshot was taken -- a test's
    `monkeypatch.setenv`, or `driver.py` folding `.env` in after this module was first
    imported. A run that does not mutate its own environment (which is every run) reads
    one object from first call to last.
    """
    global _CURRENT, _RAW
    env = os.environ
    raw = _raw(env)
    if _CURRENT is None or raw != _RAW:
        _CURRENT, _RAW = load(env), raw
    return _CURRENT


def reset() -> None:
    """Drop the snapshot. The next `current()` rebuilds from the environment.

    For tests: a test that configures the platform with `monkeypatch.setenv` and wants
    to be sure the previous test's snapshot is gone calls this.
    """
    global _CURRENT, _RAW
    _CURRENT, _RAW = None, None


# ------------------------------------------------------------ the record ---

def dump(config: Config | None = None, run: RunConfig | None = None) -> dict:
    """The effective configuration as a plain, JSON-serialisable dict.

    Credentials are **not** dumped. A secret field is recorded as `{"present": bool}` --
    never its value -- because `run_meta.json` is written to disk and read by people,
    and this repository's rule is that credentials are not written down.
    """
    cfg = current() if config is None else config
    out: dict[str, Any] = {}
    for area in _AREAS:
        value = getattr(cfg, area)
        fields_out: dict[str, Any] = {}
        for spec in fields(value):
            item = getattr(value, spec.name)
            if spec.metadata.get("secret"):
                fields_out[spec.name] = {"present": bool(item)}
            else:
                fields_out[spec.name] = item
        out[area] = fields_out
    if run is not None:
        out["run"] = asdict(run)
    return out


def describe() -> list[dict]:
    """One row per field: variable name, default, what it does.

    This is the `--help` projection of the declaration. It describes the *surface*, not
    the current values, so it says nothing an operator's environment holds; a secret
    field's default is printed as `<redacted>` anyway, and every row is marked `secret`,
    so a reader (or a test) can see which variables are credentials.
    """
    rows: list[dict] = []
    for area in _AREAS:
        for spec in fields(_AREA_CLASSES[area]()):
            meta = spec.metadata
            rows.append({
                "area": area,
                "field": spec.name,
                "variable": meta["env"],
                "default": "<redacted>" if meta.get("secret") else spec.default,
                "secret": bool(meta.get("secret")),
                "what": meta["doc"],
            })
    return rows


def run_config(args: Any, run_id: str, seed: str | None = None,
               mode: str | None = None) -> RunConfig:
    """The run's own parameters, read explicitly from the parsed argv.

    Deliberately not reflective: every attribute is named here, so a renamed flag is a
    `AttributeError` in one place rather than a silently missing field in the record.
    `mode` is an argument because the recorded value is the *resolved* mode (`driver.py`
    turns the deprecated `--mode A|B` flag into `"unified"`), not the flag.
    """
    return RunConfig(
        run_id=run_id,
        harness=args.harness,
        dataset=args.dataset,
        side=args.side,
        sampling=args.sampling,
        rounds=args.rounds,
        trials=args.trials,
        jobs=args.jobs,
        sandbox=bool(args.sandbox),
        mode=mode if mode is not None else getattr(args, "mode", ""),
        seed=seed,
        runs_root=str(args.runs_root),
        work_root=str(args.work_root),
        method_entrypoint=args.method_entrypoint,
        budget_profile=getattr(args, "budget_profile", None),
        budget_ledger=getattr(args, "budget_ledger", None),
        budget_arm=getattr(args, "budget_arm", "agent"),
        budget_profile_hash=getattr(args, "budget_profile_hash", None),
    )


__all__ = ["ENV_NAMES", "current", "describe", "dump", "load", "reset", "run_config"]
