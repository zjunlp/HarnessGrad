"""The platform's implementation, layered so the dependencies point one way.

    identity        what a measurement is               (no docker, no http, no filesystem)
    environments    what a task needs in order to run
    records         writing the record
    channel         what a method is shown
    methods         calling an external method
    loops/          the three schedules: mode A, mode B, the eval side

`driver.py` stays at the repository root and stays the entrypoint: it is the path that
INTERFACE.md, `tools/serve_ui.py` and every recorded run's argv name. Keeping a documented
entry point and moving the body inwards is what `pip` and `pytest` do, for the same reason:
the name people type does not have to be the name the code lives under.

The rule the layering keeps is that a module imports only from the layers below it, so the
identity of a measurement can be computed in a test without a docker daemon, and "what a
number means" never has to depend on how the number was produced.

Not to be confused with `_harnessgrad/`: this package is the referee, that directory is the
one-way channel the platform fills for a method inside a run workspace. The underscore is
the whole difference on a filesystem, which is why the two never appear in the same
sentence in the code.
"""

from __future__ import annotations

from pathlib import Path

#: The framework's own version, recorded on every point so a reader can tell which rules a
#: number was produced under.
FRAMEWORK_VERSION = "0.1.0"

#: The repository root. `.env`, `runs/` and `base_harness/` hang off it.
PLATFORM_ROOT = Path(__file__).resolve().parents[1]

#: The method contract's version. The platform declares it; a harness has nothing to do with
#: methods any more, so asking a harness for it would be asking the wrong artifact.
PLATFORM_API_VERSION = "0.1.0"
from .trusted_runner import IdempotencyConflict, TrustedRunnerError, TrustedRunnerFacade

__all__ = ["TrustedRunnerFacade", "TrustedRunnerError", "IdempotencyConflict"]
