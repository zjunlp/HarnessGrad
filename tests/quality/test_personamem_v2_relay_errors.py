"""Recoverable actor requests must not hide genuine PersonaMem relay failures."""
import importlib.util
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from data.datasets import personamem_v2 as dataset
from data.datasets.personamem_v2.lib.protocol import digest
from eval.service import tool_transport


def _request(socket_path, frame):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(socket_path))
        client.sendall(frame + b"\n")
        response = b""
        while b"\n" not in response:
            chunk = client.recv(4096)
            assert chunk, "relay closed before replying"
            response += chunk
    return json.loads(response.split(b"\n", 1)[0])


@pytest.mark.parametrize("scenario,clean,errors,refused", [
    pytest.param("handler", True, 2, 0, id="request-error-and-handler-failure"),
    pytest.param("serialization", True, 2, 0, id="request-error-and-serialization-failure"),
    pytest.param("parser_runtime", True, 2, 0, id="request-error-and-parser-runtime-failure"),
    pytest.param("call_budget", True, 1, 1, id="request-error-and-call-budget"),
    pytest.param("frame_budget", True, 1, 1, id="request-error-and-frame-budget"),
    pytest.param("unclean", False, 1, 0, id="request-error-and-unclean-stop"),
])
def test_genuine_failure_survives_request_errors(tmp_path, monkeypatch, scenario,
                                               clean, errors, refused):
    # Exercise the real adapter entrypoint and Unix transport. Only the stop event
    # and the source of each injected service failure are replaced; counters are real.
    spec = importlib.util.spec_from_file_location(
        "personamem_failure_relay", dataset.ASSETS / "relay.py")
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)

    payload = {"schema": "personamem-v2-input-v1", "task_id": "relay-failure-fixture",
               "chunks": ["history"], "questions": [{"id": "q1", "text": "Choose a."}]}
    payload["input_sha256"] = digest(payload)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload))
    episode = tmp_path / "episode.json"
    receipt = tmp_path / "receipt.json"
    socket_path = tmp_path / "relay.sock"
    monkeypatch.setenv("HG_RELAY_RECORD_PATH", str(episode))
    argv = ["relay.py", "--episode", str(source), "--socket", str(socket_path),
            "--receipt", str(receipt)]
    if scenario == "call_budget":
        argv += ["--max-calls", "5"]
    elif scenario == "frame_budget":
        argv += ["--max-line-bytes", "128"]
    monkeypatch.setattr(sys, "argv", argv)

    if scenario in {"handler", "serialization"}:
        original_handle = relay.Session.handle

        def handle(session, operation, args):
            if operation == "fixture_failure":
                if scenario == "handler":
                    raise RuntimeError("fixture handler failure")
                return object()
            return original_handle(session, operation, args)

        monkeypatch.setattr(relay.Session, "handle", handle)

    parser_failure_frame = b'{"op":"fixture_parser_failure","args":{}}'
    if scenario == "parser_runtime":
        original_loads = json.loads

        def loads(raw):
            if raw == parser_failure_frame:
                raise MemoryError("fixture parser runtime failure")
            return original_loads(raw)

        # Replace only the transport's JSON dependency, not the stdlib module used
        # by the adapter, client, and receipt reader in this same process.
        monkeypatch.setattr(tool_transport, "json", SimpleNamespace(
            loads=loads, dumps=json.dumps))

    def rpc(operation, args=None):
        response = _request(socket_path, json.dumps(
            {"op": operation, "args": args or {}}).encode())
        assert response["ok"], response
        return response["result"]

    def wait(deadline):
        assert _request(socket_path, b"{not-json}") == {
            "ok": False, "error": "bad_request"}
        # Complete the episode before each failure, so a nonzero exit cannot be
        # explained by an incomplete history or missing answers.
        assert rpc("next")["chunk_id"] == "chunk:0"
        assert rpc("ack", {"chunk_id": "chunk:0", "request_id": "a"}) == {"accepted": True}
        assert rpc("next")["question_id"] == "q1"
        assert rpc("submit", {"question_id": "q1", "answer": "a", "request_id": "s"}) == {
            "accepted": True}
        assert rpc("next") == {"kind": "done"}

        if scenario in {"handler", "serialization"}:
            response = _request(socket_path, b'{"op":"fixture_failure","args":{}}')
            assert response["ok"] is False
            expected = "RuntimeError: fixture handler failure" if scenario == "handler" else "result_not_serialisable:"
            assert response["error"].startswith(expected)
        elif scenario == "parser_runtime":
            assert _request(socket_path, parser_failure_frame) == {
                "ok": False, "error": "bad_request"}
        elif scenario == "call_budget":
            assert _request(socket_path, b'{"op":"next","args":{}}') == {
                "ok": False, "error": "call_budget"}
        elif scenario == "frame_budget":
            assert _request(socket_path, b"x" * 129) == {
                "ok": False, "error": "frame_too_large"}
        return clean

    stop = SimpleNamespace(wait=wait, set=lambda: None)
    monkeypatch.setattr(relay, "threading", SimpleNamespace(Event=lambda: stop))
    monkeypatch.setattr(relay, "signal", SimpleNamespace(
        SIGTERM=relay.signal.SIGTERM, SIGINT=relay.signal.SIGINT,
        signal=lambda *args: None))

    assert relay.main() == 1
    recorded = json.loads(receipt.read_text())
    assert recorded["task_id"] == payload["task_id"]
    assert recorded["stopped_cleanly"] is clean
    assert recorded["protocol_rejections"] == 0
    assert recorded["transport"]["request_errors"] == 1
    assert recorded["transport"]["errors"] == errors
    assert recorded["transport"]["refused"] == refused
    record = json.loads(episode.read_text())
    assert record["complete"] is True
    assert record["acked_chunks"] == 1
    assert record["answers"] == [{"question_id": "q1", "answer": "a"}]
    assert not socket_path.exists()
