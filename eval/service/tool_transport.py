"""One tool call over an AF_UNIX socket: the transport two datasets share.

**Why a transport and not a mount.** ALFWorld hands the agent a file that *is* the interaction
environment, because the program that talks to that environment runs inside the agent's own
sandbox: `game.tw-pddl` (read by `data/datasets/alfworld/lib/gym_env.py`). AgentDojo did the same
with `dojo.initial.json` until `data/datasets/agentdojo/relay.py` moved its environment behind
this socket; that is the measurement below. Reading such a file is not a broken scorer -- the
score is an honest replay -- but it is not a measurement of the benchmark either, because the
agent obtained the state instead of eliciting it. Measured on the archived runs: AgentDojo's 515
reading tasks average 0.2592 against 0.1406 for the 434 that did not read it.

The fix is to keep the environment in a process the agent cannot read and let it ask. This module
is the asking: newline-delimited JSON over a UNIX stream socket, one request per line, one reply
per line. It knows nothing about tools, environments or datasets -- that is the caller's handler.
Both halves are here so there is one implementation: `Relay` is what a host-side server (such as
`data/datasets/agentdojo/relay.py`) runs, and `call` is what a sandboxed client imports -- SETUP
projects this file beside `dojo.py` as `dojo_transport.py`, because the platform tree is hidden
inside the sandbox.

**The four rules that make it safe to put on the sandbox boundary.**

1. *The socket is a path, not a port.* Nothing here opens a TCP handle, so a task with
   `network="none"` can still use it, and the platform's `eval/containers/model_route.py`
   validation rules apply unchanged (absolute, non-symlink, `S_ISSOCK`, not reachable through
   writable storage).
2. *An error is never an empty observation.* A handler exception becomes
   ``{"ok": false, "error": ...}`` and `call` raises. Returning ``None`` or ``{}`` for a failed
   tool call would be indistinguishable from a tool that legitimately found nothing, which is
   exactly the failure mode this platform exists to refuse.
3. *The budget and the deadline are the server's, not the client's.* The client cannot ask for
   one more call by lying about its count, and a spent budget is reported as its own error rather
   than as an empty result.
4. *The receipt outlives the connection.* `Relay.receipt` is what lets the runner decide that a
   relay fault is an unmeasured task instead of a zero.

**What this does not do.** It does not mount itself into a sandbox, does not build the bwrap
plan, and does not decide what a dataset should serve. `eval/containers/tool_route.py` owns the
mount rules and the runner owns the lifecycle.

Wire format, exactly::

    client -> {"op": "call", "args": {...}}\\n
    server -> {"ok": true, "result": ...}\\n
    server -> {"ok": false, "error": "call_budget"}\\n

A blank line is ignored, so a caller may keep the socket open across calls. A request whose
``op`` is not a string is refused rather than coerced.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from pathlib import Path

#: Legacy defaults. Callers may declare another positive bound or None.
MAX_CALLS = 4096
MAX_LINE = 8 * 1024 * 1024
READY_TIMEOUT_S = 30.0


class ToolTransportError(RuntimeError):
    """The transport could not complete a call. Never a tool result."""


class Relay:
    """A host-side handler reachable over a UNIX socket.

    Usage::

        with Relay(sock_path, handler, max_calls=200) as relay:
            ...                     # the agent runs here
        print(relay.receipt)

    ``handler(op, args)`` runs on the server thread and returns any JSON-serialisable value.
    """

    def __init__(self, socket_path, handler, *, max_calls=MAX_CALLS, deadline_s=None,
                 ready_timeout_s=READY_TIMEOUT_S, max_line_bytes=MAX_LINE):
        for key, value in (('max_calls', max_calls), ('max_line_bytes', max_line_bytes)):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f'{key} must be a positive integer or None')
        self.path = Path(socket_path)
        self._handler = handler
        self._max_calls = max_calls
        self._max_line_bytes = max_line_bytes
        self._deadline = None if deadline_s is None else time.monotonic() + float(deadline_s)
        self._ready_timeout_s = ready_timeout_s
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = None
        self._thread = None
        self.receipt = {"calls": 0, "refused": 0, "errors": 0, "connections": 0,
                        "started_utc": None, "stopped_utc": None,
                        "limits": {"max_calls": max_calls, "max_line_bytes": max_line_bytes,
                                   "deadline_s": deadline_s}}

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> "Relay":
        """Bind, listen, and return only after the socket answers a probe.

        Readiness is proven, not assumed: the client's first call can race a freshly-bound
        socket, and a task that reaches the relay too early must fail loudly rather than be
        scored against a missing tool.
        """
        if not self.path.is_absolute():
            raise ToolTransportError(f"relay socket path must be absolute: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        if not stat.S_ISSOCK(self.path.stat().st_mode):
            raise ToolTransportError(f"relay path is not a socket: {self.path}")
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self.receipt["started_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._thread = threading.Thread(target=self._serve, name="tool-relay", daemon=True)
        self._thread.start()
        return self

    def __enter__(self) -> "Relay":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self.receipt["stopped_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # -- server --------------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            with self.receipt_lock:
                self.receipt["connections"] += 1
            try:
                self._session(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    receipt_lock = threading.Lock()

    def _session(self, conn) -> None:
        buffer = b""
        conn.settimeout(1.0)
        while not self._stop.is_set():
            try:
                chunk = conn.recv(65536)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            if self._max_line_bytes is not None and len(buffer) > self._max_line_bytes:
                with self.receipt_lock:
                    self.receipt['refused'] += 1
                self._reply(conn, {"ok": False, "error": "frame_too_large"})
                return
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                if not self._reply(conn, self._handle(line)):
                    return

    def _reply(self, conn, payload: dict) -> bool:
        try:
            raw = json.dumps(payload).encode() + b"\n"
            if self._max_line_bytes is not None and len(raw) > self._max_line_bytes:
                with self.receipt_lock:
                    self.receipt['refused'] += 1
                raw = b'{"ok": false, "error": "reply_too_large"}\n'
            conn.sendall(raw)
            return True
        except OSError:
            return False

    def _handle(self, line: bytes) -> dict:
        try:
            request = json.loads(line)
        except Exception:
            with self.receipt_lock:
                self.receipt["errors"] += 1
            return {"ok": False, "error": "bad_request"}
        if not isinstance(request, dict):
            with self.receipt_lock:
                self.receipt["errors"] += 1
            return {"ok": False, "error": "bad_request"}
        op = request.get("op")
        args = request.get("args") or {}
        if not isinstance(op, str) or not op:
            with self.receipt_lock:
                self.receipt["errors"] += 1
            return {"ok": False, "error": "bad_op"}
        if not isinstance(args, dict):
            with self.receipt_lock:
                self.receipt["errors"] += 1
            return {"ok": False, "error": "bad_args"}
        if self._deadline is not None and time.monotonic() > self._deadline:
            with self.receipt_lock:
                self.receipt["refused"] += 1
            return {"ok": False, "error": "deadline"}
        with self.receipt_lock:
            if self._max_calls is not None and self.receipt["calls"] >= self._max_calls:
                self.receipt["refused"] += 1
                return {"ok": False, "error": "call_budget"}
            self.receipt["calls"] += 1
        try:
            result = self._handler(op, args)
        except Exception as exc:  # noqa: BLE001 - a failed tool call is not an empty result
            with self.receipt_lock:
                self.receipt["errors"] += 1
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            json.dumps(result)
        except TypeError as exc:
            with self.receipt_lock:
                self.receipt["errors"] += 1
            return {"ok": False, "error": f"result_not_serialisable: {exc}"}
        return {"ok": True, "result": result}


def call(socket_path, op: str, args: dict | None = None, *, timeout_s: float | None = 60.0,
         max_line_bytes: int | None = MAX_LINE):
    """One call against a `Relay`. Raises `ToolTransportError` on any failure.

    A missing socket is a *platform* failure, not a tool that returned nothing, and it is
    reported as such: the caller turns it into an unmeasured task, never a zero.
    """
    path = Path(socket_path)
    if not path.exists():
        raise ToolTransportError(f"tool relay socket is absent: {path}")
    payload = json.dumps({"op": op, "args": args or {}}).encode() + b"\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(None if timeout_s is None else float(timeout_s))
    try:
        sock.connect(str(path))
        sock.sendall(payload)
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(65536)
            if not chunk:
                raise ToolTransportError("tool relay closed the connection mid-reply")
            buffer += chunk
            if max_line_bytes is not None and len(buffer) > max_line_bytes:
                raise ToolTransportError("tool relay reply exceeded the frame limit")
    except OSError as exc:
        raise ToolTransportError(f"tool relay unreachable: {exc}") from exc
    finally:
        try:
            sock.close()
        except OSError:
            pass
    reply = json.loads(buffer.split(b"\n", 1)[0])
    if not reply.get("ok"):
        raise ToolTransportError(str(reply.get("error") or "tool call failed"))
    return reply.get("result")


def selftest() -> int:
    """A round trip, a refused call, and a failed handler -- the three shapes a caller sees."""
    import tempfile

    failures = 0

    def handler(op, args):
        if op == "echo":
            return {"echo": args.get("x")}
        if op == "boom":
            raise ValueError("handler failed on purpose")
        if op == "unserialisable":
            return {1, 2, 3}
        raise KeyError(op)

    with tempfile.TemporaryDirectory(prefix="tool-transport-selftest-") as tmp:
        path = Path(tmp) / "tool.sock"
        with Relay(path, handler, max_calls=3) as relay:
            if call(path, "echo", {"x": 7}) != {"echo": 7}:
                print("  SELFTEST FAIL: echo round trip")
                failures += 1
            for label in ("boom", "unserialisable"):
                try:
                    call(path, label)
                except ToolTransportError:
                    pass
                else:
                    print(f"  SELFTEST FAIL: {label} did not raise")
                    failures += 1
            # max_calls=3 and three calls have been made: the fourth must be refused as a
            # budget error, not silently answered.
            try:
                call(path, "echo", {"x": 1})
            except ToolTransportError as exc:
                if "call_budget" not in str(exc):
                    print(f"  SELFTEST FAIL: budget refusal said {exc!r}")
                    failures += 1
            else:
                print("  SELFTEST FAIL: budget was not enforced")
                failures += 1
            receipt = dict(relay.receipt)
        if receipt["calls"] != 3 or receipt["errors"] != 2 or receipt["refused"] != 1:
            print(f"  SELFTEST FAIL: receipt {receipt}")
            failures += 1
        if path.exists():
            print("  SELFTEST FAIL: socket survived the context manager")
            failures += 1
        try:
            call(path, "echo", {"x": 1})
        except ToolTransportError:
            pass
        else:
            print("  SELFTEST FAIL: a call without a socket did not raise")
            failures += 1

    print(f"  selftest: {4 - failures}/4")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
