"""Validated infrastructure mounts for the host-side tool relay.

`eval/service/tool_transport.py` is the transport: one NDJSON call over one AF_UNIX
socket, handler-owned state, a receipt that can make a relay fault *unmeasured* rather
than a zero. This module is the other half. It decides whether that socket may be
mounted into a task's namespace at all, and emits the bind plan. It does not build a
relay, a client or a wrapper -- the transport serves, and the dataset CLI imports the
published transport module and calls it.

The contract, for a dataset adapter author
------------------------------------------

* **The dataset provides** an environment object and `handler(op, args) -> result`
  (any JSON value). On the host it constructs
  `tool_transport.Relay(path, handler, max_calls=...)` and passes
  `tool_socket=relay.path` to `sandbox.run`/`sandbox.command`.
* **The agent gets** two read-only files under `/run/harnessgrad-tool`: `tool.sock`
  and `tool_transport.py`. Its CLI does
  `sys.path.insert(0, "/run/harnessgrad-tool"); import tool_transport`, then
  `tool_transport.call("/run/harnessgrad-tool/tool.sock", "move", {"to": "north"})`.
  Every failure raises `ToolTransportError`; a missing socket is a *platform*
  failure and must become an unmeasured task, never a zero.
* **The state lives** in the host process that constructed the environment. It is
  never written to a workdir, never serialised into a mount, and never sent over the
  socket except as the result the handler chose to return.
* **Never materialised into the workdir**: the state, any serialisation of it, the
  handler's source, the constructor's arguments, or any file the agent could write
  that the environment would then read.

Where this deliberately differs from `model_route`
--------------------------------------------------

* **No `network == "none"` requirement.** Model-route tasks run in a network namespace
  where the validated socket is the only route to the model, so the policy is part of
  that invariant. Tool-relay datasets declare `{"kind": "files"}` and may leave
  `network` unset; a UNIX socket is not reachable over a network in the first place,
  so the rule would add nothing here and would refuse the datasets this capability
  exists for. Everything else is *shared*, not restated:
  `eval/containers/unix_socket_route.py` holds the rules and both routes call it.
* **No bootstrap.** The model route wraps the agent's argv in a trusted forwarder
  because the agent speaks HTTP to a fixed loopback URL. Here the agent imports the
  published module, so nothing is inserted into its argv and its arguments,
  environment and exit status are untouched.
* **The fixed directory is frozen.** The two files are published into a tmpfs that is
  remounted read-only afterwards, so the socket's *name* cannot be replaced either.
  Without that, bwrap creates `/run/harnessgrad-tool` as an ordinary writable
  directory in the namespace's root, and the subject can unlink `tool.sock` and bind
  its own socket at that path (and leaves a `__pycache__` beside the module).
  Measured in this checkout: with `--tmpfs` + `--remount-ro` the directory lists
  exactly `tool.sock` and `tool_transport.py`, writing either file and unlinking the
  socket raise `OSError`, and `import tool_transport` still works (it just cannot
  write bytecode).

`PLAN_VERSION` is *not* bumped by this module's existence: no dataset passes
`tool_socket` in this commit, so nothing a subject can see has changed. The first
migration that publishes a tool socket must bump it (`eval/containers/sandbox.py`),
because from then on a curve measured under the old plan and one under the new plan
are measurements of subjects in two different worlds.
"""
from __future__ import annotations

from pathlib import Path

from eval.containers import unix_socket_route
from eval.service.modelgate import GatewayUnavailable

#: Where the socket and the transport module appear *inside* the sandbox. Fixed
#: destinations, so no task path can alias them and no dataset policy can shadow them.
ROOT = Path("/run/harnessgrad-tool")
SOCKET = ROOT / "tool.sock"
TRANSPORT = ROOT / "tool_transport.py"

#: The host-side module that becomes `/run/harnessgrad-tool/tool_transport.py`. It must
#: be a real file: a symlink here would publish whatever it pointed at.
TRANSPORT_SOURCE = Path(__file__).resolve().parents[1] / "service/tool_transport.py"


def mounts(socket_path: Path, *, network=None, exposed_paths, writable_paths):
    """Revalidate at launch and return the bind plan for the socket and its module.

    `network` is accepted so one call site can hand both routes the same keyword, but
    the tool route never requires a policy: see the module docstring.
    """
    source = Path(socket_path)
    unix_socket_route.verify(source, root=ROOT, label="tool", network=network,
                             exposed_paths=exposed_paths,
                             writable_paths=writable_paths,
                             require_network_none=False)
    if TRANSPORT_SOURCE.is_symlink() or not TRANSPORT_SOURCE.is_file():
        raise GatewayUnavailable("trusted tool transport module is unavailable")
    return ["--tmpfs", str(ROOT),
            "--ro-bind", str(source), str(SOCKET),
            "--ro-bind", str(TRANSPORT_SOURCE), str(TRANSPORT),
            "--remount-ro", str(ROOT)]
