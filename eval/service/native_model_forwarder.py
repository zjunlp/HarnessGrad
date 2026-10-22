"""Trusted bootstrap inside bwrap; ordinary HTTP over private loopback to AF_UNIX.

Standalone stdlib program, executed with /usr/bin/python3 -I -S. The child keeps
its original interpreter, environment, arguments and exit status. No budgets or
HTTP payloads are changed. The host relay, not the client, chooses the upstream.
"""
from __future__ import annotations

import os
import socket
import socketserver
import subprocess
import sys
import threading


def pump(source, dest):
    try:
        while data := source.recv(65536):
            dest.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dest.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
            upstream.connect(self.server.socket_path)
            send = threading.Thread(target=pump, args=(self.request, upstream), daemon=True)
            send.start()
            pump(upstream, self.request)
            send.join()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True


def main():
    socket_path, path, *argv = sys.argv[1:]
    if not argv or argv.pop(0) != "--" or not argv:
        raise ValueError("forwarder requires an explicit child command")
    # Bind only the new namespace's loopback, never a host interface. Port 0 is
    # chosen and held before starting the client, so concurrent runs cannot race.
    with Server(("127.0.0.1", 0), Handler) as server:
        server.socket_path = socket_path
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = dict(os.environ)
        env["HG_AGENT_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}{path}"
        try:
            return subprocess.run(argv, env=env).returncode
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    code = main()
    sys.exit(code if code >= 0 else 128 - code)
