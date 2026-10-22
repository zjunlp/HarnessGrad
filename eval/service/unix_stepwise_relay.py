"""One controller-owned stepwise exchange over an actor-visible Unix socket.

Uses the model relay's bounded socket server and lifecycle. Only the socket directory
is projected read-only into the actor; sessions and receipts stay in the controller.
Newline-delimited JSON: controller observation, actor answer, next observation.
"""
from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import socket
import socketserver
import threading
import time

from eval.service.stepwise_transport import StepwiseTransportError, transport_stepwise
from eval.service.unix_model_relay import _Server


class StepwiseRelay:
    def __init__(self, directory: Path, session, *, contract: str, public_fields,
                 timeout_s: float, max_submissions: int = 256,
                 max_message_bytes: int = 4096):
        if (type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
                or timeout_s <= 0):
            raise ValueError("positive finite stepwise deadline required")
        if (type(max_submissions) is not int or not 0 < max_submissions <= 256
                or type(max_message_bytes) is not int or not 0 < max_message_bytes <= 4096):
            raise ValueError("stepwise allowance exceeds controller ceilings")
        self.path = Path(directory) / "stepwise.sock"
        self.session = session
        self.contract = contract
        self.timeout_s = timeout_s
        self.max_submissions = max_submissions
        self.max_message_bytes = max_message_bytes
        self.lock = threading.Lock()
        self.connections = 0
        self.rejected_connections = 0
        self.rejections = []
        self.active = None
        self.stopping = False
        self.started = False
        self.closed = False
        self.transport = {"status": "incomplete", "reason": "actor_did_not_connect",
                          "measured": False, "events": [], "submissions": 0}
        owner = self

        class Server(_Server):
            def verify_request(self, connection, address):
                # Reserve before spawning a handler; rejected connections neither
                # start threads nor grow an unbounded rejection-message list.
                with owner.lock:
                    if owner.stopping or owner.connections:
                        owner.rejected_connections += 1
                        owner.rejections = ["connection_budget"]
                        return False
                    owner.connections += 1
                    owner.active = connection
                    return True

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                connection = self.request
                pending = bytearray()

                def remaining():
                    seconds = owner.deadline - time.monotonic()
                    if seconds <= 0:
                        raise TimeoutError("stepwise deadline")
                    connection.settimeout(seconds)

                def deliver(wire):
                    remaining()
                    connection.sendall(wire.encode("utf-8") + b"\n")

                def receive():
                    # Recompute the absolute deadline between reads: trickled bytes
                    # must not reset a buffered readline's inactivity timeout.
                    while True:
                        remaining()
                        newline = pending.find(b"\n")
                        if newline >= 0:
                            wire = bytes(pending[:newline])
                            del pending[:newline + 1]
                            return wire.decode("utf-8")
                        capacity = owner.max_message_bytes + 2 - len(pending)
                        if capacity <= 0:
                            raise ValueError("oversized answer frame")
                        chunk = connection.recv(capacity)
                        if not chunk:
                            raise ValueError("incomplete answer frame")
                        pending.extend(chunk)

                try:
                    receipt = transport_stepwise(
                        owner.session, deliver, receive, contract=contract,
                        public_fields=public_fields, max_submissions=max_submissions,
                        max_message_bytes=max_message_bytes)
                except StepwiseTransportError as exc:
                    receipt = exc.receipt
                except Exception:
                    receipt = {"status": "incomplete", "reason": "controller_transport_failed",
                               "measured": False, "events": [], "submissions": 0}
                finally:
                    with owner.lock:
                        owner.active = None
                with owner.lock:
                    owner.transport = receipt

        os.chmod(directory, 0o700)
        self.server = Server(str(self.path), Handler)
        try:
            os.chmod(self.path, 0o600)
            self.thread = threading.Thread(
                target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        except BaseException:
            self.server.server_close()
            self.path.unlink(missing_ok=True)
            raise

    def start(self):
        self.deadline = time.monotonic() + self.timeout_s
        try:
            self.thread.start()
            self.started = True
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        if self.closed:
            return
        with self.lock:
            self.stopping = True
            if self.active is not None:
                try:
                    self.active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        try:
            if self.started:
                self.server.shutdown()
            # Like the model relay, join every handler before scoring.
            self.server.server_close()
            if self.started:
                self.thread.join()
        finally:
            self.path.unlink(missing_ok=True)
            self.closed = True

    def receipt(self):
        with self.lock:
            return {"schema": "harnessgrad.unix-stepwise-relay.v1",
                    "protocol": self.contract, "status": self.transport["status"],
                    "measured": False, "production_authorized": False,
                    "limits": {"timeout_s": self.timeout_s, "max_connections": 1,
                               "max_submissions": self.max_submissions,
                               "max_message_bytes": self.max_message_bytes},
                    "connections": self.connections, "rejections": list(self.rejections),
                    "rejected_connections": self.rejected_connections,
                    "transport": deepcopy(self.transport),
                    "cleanup": {"server_closed": self.closed,
                                "thread_joined": self.closed and not self.thread.is_alive(),
                                "socket_removed": self.closed and not self.path.exists()}}
