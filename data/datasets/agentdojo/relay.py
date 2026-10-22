"""Host-side AgentDojo environment server: the environment is not on the agent's disk.

Run by the platform **outside** the sandbox, with the interpreter the verifier already
trusts (``DATASET_ROOT/runtime/bin/python``, see ``data/datasets/agentdojo.py``)::

    python relay.py --case <PREPARED>/<i>/case.json --socket <absolute path> [--deadline-s N]

**The defect this replaces, measured.** Until this program existed, ``SETUP`` materialised
``dojo.initial.json`` -- ``bridge.snapshot(case)``, the whole initial environment (inbox,
calendar, cloud drive, bank, slack, web) plus the tool catalogue, with the registered
``ignore_previous`` payload verbatim inside ``environment`` -- into the agent's working
directory, because ``cli/dojo`` was a program the agent ran and read that file from its cwd.
The agent could therefore read the whole prepared environment instead of eliciting it
through tools. Measured on 949 archived tasks: the 515 that read the file average utility
0.2592, the 434 that did not average 0.1406. ``attack_success`` stopped measuring an
instruction delivered inside a tool response and measured one the agent could read directly.
The verifier never needed the file: ``VERIFY.inputs`` is check.py + bridge.py + case.json +
the trusted runtime, and ``bridge.evaluate`` rebuilds the environment from ``case.json``
with ``load_and_inject_default_environment``. This module is the missing half -- a trusted
service that owns the environment and answers questions about it.

**The environment is built here and stays here.** ``build_handler`` calls
``bridge.get_case`` and ``user.init_environment(suite.load_and_inject_default_environment(
injections))`` -- the same two calls ``bridge.snapshot``/``bridge.evaluate`` make -- so the
object the tools mutate is the object the verifier would have rebuilt, and it lives only in
this process. Tool calls mutate it in place, which is the continuity ``dojo.state.json`` was
meant to carry between the agent's commands (and, for the workspace suite, did not -- see the
measured difference below). Nothing is written to disk: no snapshot, no state file, no
receipt under a path the agent can read.

**The op set is closed, and no op returns the environment.**

* ``{"op": "tools"}`` -> the tool catalogue (name, description, parameters), i.e. what
  ``dojo.py tools`` printed.
* ``{"op": "call", "args": {"function": name, "arguments": {...}}}`` -> one tool's result,
  ``{"result": ..., "error": ...}``, i.e. what ``run_function`` returned to ``dojo.py call``
  before this change (including its ``default=str`` rendering of pydantic results).

Everything else raises ``UnsupportedOp`` and the transport returns an error frame -- there
is no op that dumps the environment, returns it, or reflects over it. The handler is the
only thing the socket can reach, and the environment object is a closure variable of it.

**One difference from the deleted state file, measured and deliberate.** ``dojo.state.json``
was ``env.model_dump(mode="json")`` re-validated by the next command, and every collection in
``WorkspaceEnvironment`` re-derives its dict from ``initial_*`` in a pydantic ``after``
validator (``Inbox._create_emails``, ``Calendar._create_events``,
``CloudDrive._create_files``). Re-validating therefore silently reset the workspace -- inbox,
calendar and cloud drive -- on every command: deleting the same email twice succeeded twice for
the agent. This process keeps one live environment, which is exactly what
``bridge.evaluate``'s ``ReplayPipeline`` does with the transcript, so the agent's observations
and the verifier's re-execution now agree. Banking, travel and slack never re-derived (their
collections are plain fields), so for them this is the same world the state file persisted.

**What is still needed outside this file**, stated rather than faked: the runner must
(a) start this program with the case before the agent does and wait for the socket -- building
the environment takes a measured ~2.1 s here, so a harness that starts first loses its first
calls; (b) publish the socket's directory into the sandbox at ``/run/harnessgrad-tool`` as
``tool.sock`` plus ``tool_transport.py``, which is what ``cli/dojo`` imports; and (c) treat a
relay fault -- no socket, this program exiting nonzero, or a receipt whose
``errors``/``refused`` counters are non-zero -- as an **unmeasured** task, never as a zero.
``relay.receipt`` is printed to stdout when this process stops, so the runner can read it; the
transport already reports errors, budget refusals and missed deadlines as errors rather than
empty results.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

# `bridge.py` lives beside this file and the transport lives in the platform's one
# implementation, `eval/service/tool_transport.py`. Both are appended, never inserted, so a
# stray module name in the repository cannot shadow the runtime's own imports.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
for _path in (str(_HERE), str(_ROOT)):
    if _path not in sys.path:
        sys.path.append(_path)

import bridge  # noqa: E402 - path set above; the trusted snapshot builder, reused not copied
from agentdojo.functions_runtime import FunctionsRuntime  # noqa: E402 - dataset runtime only
from eval.service.tool_transport import MAX_CALLS, Relay  # noqa: E402 - the one transport

#: The closed op set. A test asserts this is exactly what the handler serves.
OPS = frozenset({"tools", "call"})


class UnsupportedOp(RuntimeError):
    """An op this relay does not serve. Never an empty result."""


def build_handler(case_path):
    """The environment, in this process only, and the closed handler over it."""
    case = json.loads(Path(case_path).read_text())
    suite, user, _injection, injections = bridge.get_case(case)
    environment = user.init_environment(suite.load_and_inject_default_environment(injections))
    # `suite.tools` is `[make_function(t) for t in TOOLS]` -- the same list the deleted
    # agent-side CLI built from the environment module, so tool behaviour is unchanged.
    runtime = FunctionsRuntime(suite.tools)
    catalogue = [{"name": tool.name, "description": tool.description,
                  "parameters": tool.parameters.model_json_schema()} for tool in suite.tools]

    def handle(op, args):
        if op == "tools":
            return catalogue
        if op == "call":
            return _call(runtime, environment, args)
        raise UnsupportedOp(f"unknown op {op!r}; this relay serves only {sorted(OPS)}")

    return handle


def _call(runtime, environment, args):
    """One tool call: exactly what ``dojo.py call`` used to do with ``run_function``."""
    function = args.get("function")
    arguments = args.get("arguments")
    if not isinstance(function, str) or not function:
        raise ValueError("call op needs args.function as a non-empty string")
    if not isinstance(arguments, dict):
        raise ValueError("call op needs args.arguments as an object")
    result, error = runtime.run_function(environment, function, arguments)
    # The transport refuses a non-JSON return, and the old CLI rendered with `default=str`.
    # Round-trip here so the agent sees byte-identical JSON to what it used to see.
    return json.loads(json.dumps({"result": result, "error": error}, default=str))


def serve(case_path, socket_path, *, deadline_s=None, max_calls=MAX_CALLS):
    """Build the environment, serve until signalled or past the deadline, return the receipt."""
    handler = build_handler(case_path)  # before the socket exists: a build fault is not a served task
    stop = threading.Event()

    def _stop(_signum, _frame):
        stop.set()

    previous = {sig: signal.signal(sig, _stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    started = time.monotonic()
    try:
        with Relay(socket_path, handler, max_calls=max_calls, deadline_s=deadline_s) as relay:
            while not stop.is_set():
                if deadline_s is not None and time.monotonic() - started >= deadline_s:
                    break
                stop.wait(0.2)
    finally:
        for sig, handler_fn in previous.items():
            signal.signal(sig, handler_fn)
    return relay.receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve one AgentDojo case's environment "
                                                 "over a UNIX socket.")
    parser.add_argument("--case", required=True, help="prepared <i>/case.json")
    parser.add_argument("--socket", required=True, help="absolute path to serve on")
    parser.add_argument("--deadline-s", type=float, default=None)
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    # The runner reads this file rather than stdout: a relay that is SIGKILLed prints nothing,
    # and "no receipt" is how the runner distinguishes "died" from "served and stopped".
    parser.add_argument("--receipt", default=None,
                        help="write the call/refusal/rejection counts here on exit")
    args = parser.parse_args(argv)
    if not Path(args.case).is_file():
        raise SystemExit(f"relay: no such case: {args.case}")
    receipt = serve(args.case, args.socket, deadline_s=args.deadline_s,
                    max_calls=args.max_calls)
    text = json.dumps(receipt, indent=1, sort_keys=True)
    if args.receipt:
        Path(args.receipt).write_text(text + "\n")
    print(text)
    return 0 if not (receipt["errors"] or receipt["refused"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
