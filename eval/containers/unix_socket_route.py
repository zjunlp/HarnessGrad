"""The launch-time rules for any AF_UNIX infrastructure socket the sandbox mounts.

`model_route` and `tool_route` publish different sockets for different reasons, but
they ask the same question about the file they are about to bind: *can this socket be
swapped, aliased, or reached through storage the task can write?* A second copy of
those rules would drift from the first, so both routes call `verify` here and only
the mount destinations (and the model route's trusted forwarder check) stay local.

The rules, in the order they are applied:

1. **An absolute, non-symlink path.** A relative source resolves against whatever cwd
   the launch happens to have; a symlink lets the thing pointed at change between
   validation and execution. The model route also requires `network == "none"` here;
   the tool route does not, and `require_network_none` is the one rule the two
   genuinely disagree about (see `verify`).
2. **`S_ISSOCK`.** A regular file at the source path would be mounted read-only and
   silently serve nothing.
3. **No target overlapping this route's `ROOT`.** The mount destination is fixed, so
   an exposed path that contains it, is it, or is inside it would be mounted over
   later, or would cover it.
4. **The source is not reachable through writable storage.** If a task can write an
   ancestor of the socket, it can replace the mount's source between launch and use.

Rule 3 is the reason `ROOT` is a parameter rather than a constant: each route must
refuse overlap with *its own* fixed destination, and the model route's `ROOT` is not
the tool route's.
"""
from __future__ import annotations

from pathlib import Path
import stat

from eval.service.modelgate import GatewayUnavailable


def verify(source, *, root, label, network, exposed_paths, writable_paths,
           require_network_none=True):
    """Raise `GatewayUnavailable` unless this socket may be mounted.

    `label` is the noun the refusal speaks in ("model", "tool"), so an error names the
    route that refused instead of a generic one. `mounts` in either route is a thin
    wrapper: validate here, then emit the bind plan.

    `require_network_none` is a parameter and not a second copy of this function,
    because the two routes disagree about exactly this and nothing else. The **model**
    route needs the rule: its transport is an HTTP forwarder over loopback, so a task
    with a network would have a second, unvalidated route to the platform's endpoint,
    and the whole point of the model route is that the validated socket is the only
    way to reach the model. The **tool** route does not: the datasets that serve an
    environment over the tool relay declare `{"kind": "files"}` and may leave `network`
    unset, so demanding `network == "none"` would refuse the datasets this capability
    exists for -- and the next adapter would loosen the check rather than declare a
    policy it does not need. A UNIX socket is not reachable over a network anyway, so
    what the rule adds for a tool socket is nothing; isolating the *task's* network is
    the task's own business, and `sandbox.command` still refuses any policy that is not
    `None` or `"none"`.
    """
    source = Path(source)
    if ((require_network_none and network != "none")
            or not source.is_absolute() or ".." in source.parts
            or any(p.is_symlink() for p in (source, *source.parents))):
        if require_network_none:
            raise GatewayUnavailable(
                f"{label} socket requires network none and an absolute non-symlink source")
        raise GatewayUnavailable(
            f"{label} socket requires an absolute non-symlink source")
    if not stat.S_ISSOCK(source.stat().st_mode):
        raise GatewayUnavailable(f"{label} route source must be a UNIX socket")
    for path in exposed_paths:
        path = Path(path).resolve()
        if root == path or root in path.parents or path in root.parents:
            raise GatewayUnavailable(f"{label} route target overlaps another mount")
    for path in writable_paths:
        path = Path(path).resolve()
        if source == path or path in source.parents:
            raise GatewayUnavailable(
                f"{label} socket source is reachable through writable storage")
