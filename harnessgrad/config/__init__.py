"""The platform's typed configuration layer.

Before this package, the platform's configuration surface was 38 `HG_*` environment
variables read with `os.environ.get(...)` at the point of use: nothing declared what
the surface *was*, nothing validated it, and nothing on disk said what a run ran under.
Three defects in one week came from that shape -- a `--improver deepseek` classified as
a command-line tool because a field was never recorded, a shell variable silently
overriding what the platform had resolved, and a harness never told its own time budget
so an adapter guessed and raced the platform's kill.

The layer is four things, all derived from one declaration (`areas.py`):

    load(mapping)   environment -> dataclasses; default is `os.environ`, so a test can
                    build a configuration without touching the process environment.
    current()       the process snapshot: built once, so every reader in a run sees the
                    same value of every variable. `reset()` drops it for tests.
    dump()          the effective configuration as a JSON-serialisable dict. This is
                    what is written into `run_meta.json` under `config`. Credentials are
                    recorded as `{"present": bool}` and never by value.
    describe()      one row per field: variable name, default, one line of prose. This
                    is what `--help` prints and what `driver.py --show-config` is built
                    from.

The variable names and defaults are exactly the ones the point-of-use reads had; this
is a refactor of *where* configuration is read, not a redesign of what it means. No
variable was renamed, so an operator's `.env` keeps working unchanged.

Public API::

    from harnessgrad.config import current, dump, load, describe, reset, run_config

    cfg = current()
    cfg.agent.model            # a typed field
    dump()                     # -> dict for run_meta.json
    load({"HG_AGENT_MODEL": "m"})   # a test's own environment
"""
from __future__ import annotations

from harnessgrad.config.areas import (
    AgentConfig, Config, EgressConfig, EnvironmentConfig, ImproverConfig,
    MethodConfig, RunConfig, RuntimeConfig, SandboxConfig, StorageConfig,
)
from harnessgrad.config.loader import (
    ENV_NAMES, current, describe, dump, load, reset, run_config,
)

__all__ = [
    # the four entry points
    "load", "current", "dump", "describe",
    # test hooks and the run's argv-derived half
    "reset", "run_config", "ENV_NAMES",
    # the declaration
    "Config", "AgentConfig", "MethodConfig", "ImproverConfig", "EnvironmentConfig",
    "SandboxConfig", "EgressConfig", "RuntimeConfig", "StorageConfig", "RunConfig",
]
