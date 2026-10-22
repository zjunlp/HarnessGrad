"""One task's AF_UNIX transport to a fixed platform model endpoint.

This is infrastructure, not a task network capability. Raw duplex forwarding
preserves HTTP streaming and the harness's own budgets. Neither HTTP Host nor
absolute request targets can select a different TCP destination. No proxy CONNECT
or redirect is interpreted here. The existing model gateway pump supplies stats.
"""
from __future__ import annotations

import os
import shutil
import socket
import socketserver
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from eval.service.modelgate import GatewayStats, GatewayUnavailable, _pump


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        owner = self.server.owner
        owner.stats.connection()
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Track both ends before connecting so stop() can also cancel a handshake.
        with owner.lock:
            if owner.stopped:
                upstream.close()
                return
            owner.connections.update((self.request, upstream))
        try:
            upstream.settimeout(15)
            upstream.connect(owner.target)
            upstream.settimeout(None)
            conn = {}
            client_reader, upstream_reader = self.request, upstream
            if owner.http is not None:
                from eval.service.http_observation import ObservedReader
                observation = owner.http.connection()
                client_reader = ObservedReader(self.request, observation, 'client')
                upstream_reader = ObservedReader(upstream, observation, 'upstream')
            send = threading.Thread(target=_pump, args=(
                client_reader, upstream, "client", owner.stats, conn), daemon=True)
            send.start()
            _pump(upstream_reader, self.request, "upstream", owner.stats, conn)
            send.join()
        except OSError as exc:
            owner.stats.connect_failed(exc)
        finally:
            upstream.close()
            with owner.lock:
                owner.connections.difference_update((self.request, upstream))


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class NativeModelRoute:
    def __init__(self, base_url: str, *, observe_http=False):
        parsed = urlsplit(base_url)
        # TLS/userinfo need a different transport contract. Fail rather than
        # quietly dropping encryption, credentials or URL semantics.
        if (parsed.scheme != "http" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise GatewayUnavailable("native offline model route requires a plain HTTP base URL")
        self.target = (parsed.hostname, parsed.port or 80)
        self.path = parsed.path
        self.stats = GatewayStats()
        self.http = None
        if observe_http:
            from eval.service.http_observation import HTTPObservation
            self.http = HTTPObservation()
        self.directory = None
        self.socket_path = None
        self.server = None
        self.thread = None
        self.lock = threading.Lock()
        self.connections = set()
        self.stopped = False

    def start(self):
        try:
            self.directory = Path(tempfile.mkdtemp(prefix="hg-native-model-"))
            self.socket_path = self.directory / "model.sock"
            self.server = _Server(str(self.socket_path), _Handler)
            self.server.owner = self
            os.chmod(self.socket_path, 0o600)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            return self
        except OSError as exc:
            self.stop()
            raise GatewayUnavailable(f"native offline model socket unavailable: {exc}") from exc

    def account(self):
        return {"transport": "unix+isolated-loopback", **self.stats.as_dict()}

    def stop(self):
        with self.lock:
            self.stopped = True
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join(timeout=5)
            self.thread = None
        if self.server is not None:
            self.server.server_close()
            self.server = None
        if self.directory is not None:
            shutil.rmtree(self.directory)
            self.directory = None
