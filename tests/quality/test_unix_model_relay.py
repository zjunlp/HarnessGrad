"""Real AF_UNIX requests with a fake provider; TCP/model access is forbidden."""
from __future__ import annotations

import concurrent.futures
import contextlib
import http.client
import io
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eval.service import unix_model_relay as relay_mod

MODEL = "SYNTHETIC_OFFLINE_PROVIDER"


class Provider:
    def __init__(self, payload=None, status=200, failure=None):
        self.payload = payload if payload is not None else {
            "choices": [{"message": {"role": "assistant", "content": "fixture"}}],
            "usage": {"completion_tokens": 1},
        }
        self.status, self.failure = status, failure
        self.requests, self.connections = [], []

    def __call__(self, host, port, *, timeout):
        assert (host, port, timeout) == ("127.0.0.1", 8002, 10)
        owner = self

        class Connection:
            def __init__(self):
                self.closed = False
                self.close_event = threading.Event()

            def request(self, method, path, body, headers):
                owner.requests.append((method, path, json.loads(body), headers))
                if owner.failure:
                    raise owner.failure

            def getresponse(self):
                payload = (owner.payload if isinstance(owner.payload, bytes)
                           else json.dumps(owner.payload).encode())
                response = io.BytesIO(payload)
                response.status = owner.status
                return response

            def close(self):
                self.closed = True
                self.close_event.set()

        conn = Connection()
        self.connections.append(conn)
        return conn


@pytest.fixture
def start_relay(monkeypatch, tmp_path_factory):
    def forbid_tcp(*args, **kwargs):
        raise AssertionError("TCP/model access forbidden in offline relay tests")

    monkeypatch.setattr(socket, "create_connection", forbid_tcp)
    # The budget tests start at most eight clients and synchronize only after
    # connecting. Reserve enough pending slots even if accept is descheduled;
    # the default backlog of five can strand clients outside that barrier.
    monkeypatch.setattr(relay_mod._Server, "request_queue_size", 8)
    with contextlib.ExitStack() as stack:
        def start(provider=None, **limits):
            provider = provider if provider is not None else Provider()
            directory = tmp_path_factory.mktemp("ux")
            relay = stack.enter_context(relay_mod.Relay(
                directory, model=MODEL, _connect=provider, **limits))
            return relay, provider

        yield start


def request(relay, changes=None, *, raw=None, route=relay_mod.ROUTE, headers=None,
            before_send=None):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": "public fixture"}],
               "max_tokens": 2}
    payload.update(changes or {})
    body = json.dumps(payload).encode() if raw is None else raw
    # Inject an AF_UNIX socket; HTTPConnection.connect must never open TCP here.
    conn = http.client.HTTPConnection("unix.invalid", timeout=5)
    conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.sock.settimeout(5)
    try:
        conn.sock.connect(str(relay.path))
        if before_send is not None:
            before_send()
        try:
            conn.request("POST", route, body=body, headers=headers or {})
        except BrokenPipeError:
            # Header-only rejection may close before HTTPConnection's separate
            # body send. Read and assert the actual response; a missing or
            # malformed response must still fail below.
            pass
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_fixed_route_model_and_headers_with_private_socket(start_relay):
    relay, provider = start_relay()
    assert relay.path.parent.stat().st_mode & 0o777 == 0o700
    assert relay.path.stat().st_mode & 0o777 == 0o600
    status, body = request(relay, headers={"Authorization": "secret-fixture",
                                         "X-Caller-Context": "private-fixture"})
    assert status == 200 and body["choices"][0]["message"]["content"] == "fixture"
    method, route, payload, headers = provider.requests[0]
    assert (method, route, payload["model"]) == ("POST", relay_mod.ROUTE, MODEL)
    assert headers == {"Content-Type": "application/json", "Connection": "close"}
    receipt = relay.receipt()
    assert receipt["calls"] == 1 and receipt["reserved_output_tokens"] == 2
    assert receipt["reported_completion_tokens"] == 1
    assert "input tokens unmeasured" in receipt["input_budget"]


@pytest.mark.parametrize("change", [
    {"model": "caller-model"}, {"base_url": "http://caller.invalid"},
    {"stream": True}, {"stream": 0}, {"n": 2}, {"n": True},
    {"max_tokens": 513}, {"max_tokens": 0}, {"max_tokens": -1},
    {"max_tokens": True}, {"max_tokens": 2.0},
    {"temperature": True}, {"temperature": float("nan")},
    {"temperature": float("inf")}, {"temperature": 10 ** 400},
    {"messages": []}, {"messages": [{"role": "tool", "content": "fixture"}]},
    {"messages": [{"role": "user", "content": ["fixture"]}]},
    {"messages": [{"role": "user", "content": "fixture", "tests": "fixture"}]},
])
def test_invalid_requests_never_reserve_or_forward(start_relay, change):
    relay, provider = start_relay()
    status, _ = request(relay, change)
    assert status == 400
    assert provider.requests == [] and relay.receipt()["calls"] == 0


@pytest.mark.parametrize("raw,route,headers,status", [
    (b"{}", "/caller-route", {}, 403),
    (b"not json", relay_mod.ROUTE, {}, 400),
    (b"null", relay_mod.ROUTE, {}, 400),
    (b"[" * 2000 + b"0" + b"]" * 2000, relay_mod.ROUTE, {}, 400),
    (b"x" * (relay_mod.MAX_BODY + 1), relay_mod.ROUTE, {}, 413),
    (b"{}", relay_mod.ROUTE, {"Transfer-Encoding": "chunked"}, 400),
    (b"{}", relay_mod.ROUTE, {"Content-Length": "-1"}, 413),
    (b"{}", relay_mod.ROUTE, {"Content-Length": "not-a-number"}, 413),
])
def test_route_framing_and_size_rejected_before_forward(start_relay, raw, route, headers, status):
    relay, provider = start_relay()
    assert request(relay, raw=raw, route=route, headers=headers)[0] == status
    assert provider.requests == [] and relay.receipt()["calls"] == 0


def test_early_rejection_is_read_after_the_server_closes(start_relay, monkeypatch):
    closed = threading.Event()
    shutdown = relay_mod._Server.shutdown_request
    send = http.client.HTTPConnection.send

    def observed_shutdown(server, connection):
        shutdown(server, connection)
        closed.set()

    def delayed_body(connection, data):
        if data == b"{}":
            assert closed.wait(timeout=5)
        return send(connection, data)

    monkeypatch.setattr(relay_mod._Server, "shutdown_request", observed_shutdown)
    monkeypatch.setattr(http.client.HTTPConnection, "send", delayed_body)
    relay, provider = start_relay()
    assert request(relay, raw=b"{}", route="/caller-route") == (403, {"error": "route"})
    assert provider.requests == [] and relay.receipt()["calls"] == 0


def test_duplicate_model_keys_are_rejected_before_forward(start_relay):
    relay, provider = start_relay()
    raw = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": "fixture"}],
                      "max_tokens": 2}).encode()
    raw = b'{"model":"caller-model",' + raw[1:]
    assert request(relay, raw=raw)[0] == 400
    assert provider.requests == [] and relay.receipt()["calls"] == 0


def test_calls_are_reserved_before_provider_connection_and_exact_cap_refuses(start_relay):
    provider = Provider()
    relays = []

    def connect(*args, **kwargs):
        receipt = relays[0].receipt()
        assert receipt["calls"] == len(provider.requests) + 1
        assert receipt["reserved_output_tokens"] == receipt["calls"] * 2
        return provider(*args, **kwargs)

    relay, _ = start_relay(connect, max_calls=2)
    relays.append(relay)
    assert [request(relay)[0] for _ in range(3)] == [200, 200, 429]
    assert relay.receipt()["rejections"] == ["call_budget"]
    assert len(provider.requests) == 2


def test_declared_smoke_ceilings_are_enforced(start_relay):
    relay, provider = start_relay()
    assert [request(relay, {"max_tokens": 512})[0] for _ in range(5)] == [200] * 4 + [429]
    receipt = relay.receipt()
    assert receipt["calls"] == 4 and receipt["reserved_output_tokens"] == 2048
    assert len(provider.requests) == 4


def test_requested_token_cap_is_not_refunded_by_small_completions(start_relay):
    relay, provider = start_relay(max_total_tokens=3)
    assert request(relay)[0] == 200
    assert request(relay)[0] == 429
    assert request(relay, {"max_tokens": 1})[0] == 200
    receipt = relay.receipt()
    assert receipt["reserved_output_tokens"] == 3 and receipt["calls"] == 2
    assert receipt["rejections"] == ["token_budget"]
    assert len(provider.requests) == 2


@pytest.mark.parametrize("limits,successes,reason", [
    ({"max_calls": 2}, 2, "call_budget"),
    ({"max_calls": 4, "max_total_tokens": 3}, 1, "token_budget"),
])
def test_parallel_requests_cannot_overspend(start_relay, limits, successes, reason):
    relay, provider = start_relay(**limits)
    barrier = threading.Barrier(8)

    def send(_):
        return request(relay, before_send=lambda: barrier.wait(timeout=5))[0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(send, range(8)))
    assert statuses.count(200) == successes and statuses.count(429) == 8 - successes
    assert len(provider.requests) == successes
    receipt = relay.receipt()
    assert receipt["calls"] == successes
    assert receipt["reserved_output_tokens"] == successes * 2
    assert set(receipt["rejections"]) == {reason}


def test_parallel_clients_do_not_depend_on_accept_loop_scheduling(start_relay, monkeypatch):
    release = threading.Event()
    accept = relay_mod._Server.get_request

    def gated_accept(server):
        assert release.wait(timeout=5)
        return accept(server)

    monkeypatch.setattr(relay_mod._Server, "get_request", gated_accept)
    relay, provider = start_relay(max_calls=2)
    barrier = threading.Barrier(8, action=release.set)

    def send(_):
        return request(relay, before_send=lambda: barrier.wait(timeout=5))[0]

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(send, range(8)))
    finally:
        release.set()
    assert statuses.count(200) == 2 and statuses.count(429) == 6
    assert len(provider.requests) == 2
    assert relay.receipt()["calls"] == 2
    assert relay.receipt()["reserved_output_tokens"] == 4


@pytest.mark.parametrize("provider", [
    Provider(failure=ConnectionError("sensitive upstream context")),
    Provider(payload=b"not-json: sensitive upstream context"),
    Provider(payload=b"null"),
    Provider(payload=b"[]"),
    Provider(payload=b"[" * 2000 + b"0" + b"]" * 2000),
    Provider(payload=b'{"usage":{"completion_tokens":513,"completion_tokens":1}}'),
    Provider(payload=b"x" * (relay_mod.MAX_RESPONSE + 1)),
    Provider(payload={"error": "sensitive upstream context"}, status=503),
])
def test_failed_or_unknown_requests_keep_reservations_and_redact_errors(start_relay, provider):
    relay, provider = start_relay(provider, max_calls=1)
    status, body = request(relay)
    assert status == 502 and "sensitive" not in json.dumps(body)
    assert provider.connections[0].close_event.wait(timeout=5)
    assert request(relay)[0] == 429
    receipt = relay.receipt()
    assert receipt["calls"] == 1 and receipt["reserved_output_tokens"] == 2
    assert receipt["unknown_usage_calls"] == 1
    assert all(conn.closed for conn in provider.connections)


def test_provider_token_overrun_blocks_remaining_calls(start_relay):
    relay, provider = start_relay(Provider(payload={"usage": {"completion_tokens": 3}}))
    assert request(relay) == (502, {"error": "provider_token_overrun"})
    assert request(relay)[0] == 429
    receipt = relay.receipt()
    assert len(provider.requests) == 1 and receipt["reported_completion_tokens"] == 3
    assert receipt["unknown_usage_calls"] == 0


@pytest.mark.parametrize("used", [None, -1, True, 1.0, "1"])
def test_missing_or_invalid_usage_remains_unmeasured(start_relay, used):
    relay, provider = start_relay(Provider(payload={"usage": {"completion_tokens": used}}))
    assert request(relay)[0] == 200
    assert provider.connections[0].close_event.wait(timeout=5)
    receipt = relay.receipt()
    assert receipt["unknown_usage_calls"] == 1
    assert receipt["reported_completion_tokens"] == 0
    assert receipt["reserved_output_tokens"] == 2


def test_shutdown_waits_for_pending_forward_and_removes_socket(start_relay):
    entered, finish = threading.Event(), threading.Event()
    provider = Provider()

    def delayed(*args, **kwargs):
        entered.set()
        assert finish.wait(timeout=5)
        return provider(*args, **kwargs)

    relay, _ = start_relay(delayed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(request, relay)
        assert entered.wait(timeout=5)
        relay.server.shutdown()
        closing = pool.submit(relay.server.server_close)
        try:
            with pytest.raises(concurrent.futures.TimeoutError):
                closing.result(timeout=0.1)
        finally:
            finish.set()
        assert pending.result(timeout=5)[0] == 200
        closing.result(timeout=5)
    # The context exit also joins serve_forever and removes the socket.
    relay.__exit__(None, None, None)
    assert not relay.path.exists()
