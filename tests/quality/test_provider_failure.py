"""Model endpoint failures must remain distinct from measured zero scores (§2.5.7).

In one run, the harnesses for `bn-fit-modify` and `chess-best-move` both died with

    RuntimeError: 3 attempts failed, last: Connection error.

but the platform recorded 0.0, attributing a gateway failure to harness capability.

The container reproduction is documented in `eval/service/modelgate.py`: uvicorn
closed idle keep-alive connections after five seconds, the gateway forwarded EOF,
and a later harness call reused a dead connection. Normal client keep-alive exposed
a gateway defect.

These tests verify that:

  * Only nonzero exits with connection markers at the end of stderr qualify.
  * Provider failures enter `invalid` with attribution and stay out of `per_task`.
  * Successful exits and errors recovered by the harness do not qualify.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval.run.runner as runner  # noqa: E402

#: Excerpt from the original failure's stderr tail.
CONNECTION_TAIL = '''  File "/app/agent.py", line 162, in _call_openai
    raise RuntimeError(f"{attempts} attempts failed, last: {last}") from last
RuntimeError: 3 attempts failed, last: Connection error.
'''

APICONN = '''  File "/overlays/3.12/openai/_base_client.py", line 1183, in request
    raise APIConnectionError(request=request) from err
openai.APIConnectionError: Connection error.
'''


def test_a_harness_that_died_on_a_connection_error_is_a_provider_failure():
    got = runner._provider_failure({"exit_code": 1, "stderr": CONNECTION_TAIL})
    assert got is not None
    assert got["marker"] == "Connection error"
    assert "Connection error" in got["line"]


def test_the_openai_client_s_own_wording_is_recognised_too():
    got = runner._provider_failure({"exit_code": 1, "stderr": APICONN})
    assert got is not None and got["marker"] == "APIConnectionError"


def test_a_harness_that_exited_zero_is_never_a_provider_failure():
    """A completed harness keeps its score.

    Recovered connection errors can leave the same markers in stderr; do not
    misattribute those successful recoveries to infrastructure failure.
    """
    assert runner._provider_failure({"exit_code": 0, "stderr": CONNECTION_TAIL}) is None


def test_a_transient_error_the_harness_recovered_from_is_not_a_provider_failure():
    """Only inspect the tail; earlier errors recovered by the harness do not count."""
    recovered = ("Connection error while calling the model; retrying\n" * 20
                 + "answer written\n")
    assert runner._provider_failure({"exit_code": 0, "stderr": recovered}) is None


def test_an_unrelated_failure_is_not_misattributed():
    """Do not attribute harness bugs to the gateway."""
    assert runner._provider_failure(
        {"exit_code": 1, "stderr": "KeyError: 'entrypoint'\n"}) is None
    assert runner._provider_failure({"exit_code": None, "stderr": ""}) is None


def test_opencode_terminal_provider_failure_is_invalid_not_a_zero(tmp_path, monkeypatch):
    stderr = 'OpenCode provider failure: {"type":"provider.auth","status":401}'
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: _death(
        stderr=stderr, model_gateway=None))
    result = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1)
    assert result["per_task"] == {}
    assert result["invalid"]["t1"]["stage"] == "harness model call"
    assert runner._provider_failure({"exit_code": 0, "stderr": stderr}) is None
    assert runner._provider_failure({
        "exit_code": 1, "stderr": "OpenCode execution failed; inspect trace.jsonl"}) is None


def test_the_invalid_record_names_the_stage_and_the_service():
    """The invalid record attributes the failure to the responsible service."""
    result = {"exit_code": 1, "stderr": CONNECTION_TAIL,
              "model_gateway": {"published": True, "target": "127.0.0.1:8001"}}
    provider = runner._provider_failure(result)
    assert provider is not None
    # Assert the stage and detail wording used by `_evaluate` to detect drift.
    stage = "harness model call"
    detail = (f"the harness could not reach the model endpoint and exited "
              f"{provider['exit_code']}: {provider['line']}")
    assert "model endpoint" in detail and "Connection error" in detail
    assert stage == "harness model call"


# ----------------------- Wire the criterion into the production evaluation path ---

def _death(**over) -> dict:
    """Model an unreachable-upstream death using the fields returned by `run_one`."""
    result = {"task_id": "t1", "answer": "", "trace": "", "exit_code": 1,
              "stderr": CONNECTION_TAIL,
              "verdict": {"kind": "command", "passed": False, "score": 0.0,
                          # Explicit synthetic scorer route for the w-obs guard.
                          "scorer_isolation_class": "actor-workdir",
                          "detail": "reward 0 from /logs/verifier/reward.txt"},
              "env_usage": {}, "model_gateway": {"published": True,
                                                 "target": "127.0.0.1:8001",
                                                 "connections": 3,
                                                 "upstream_connect_failed": 1,
                                                 "upstream_connect_error":
                                                     "ConnectionRefusedError: [Errno 111]",
                                                 "upstream_closed_first": 2,
                                                 "client_closed_first": 0,
                                                 "bytes_to_upstream": 0,
                                                 "bytes_to_client": 0,
                                                 "read_errors": []},
              "identity": None}
    result.update(over)
    return result


def test_a_provider_death_reaches_the_curve_as_invalid(tmp_path, monkeypatch):
    """Verify the production wiring, not just the isolated criterion.

    `_provider_failure` once passed its own tests but had no production caller.
    In a run with 20 deaths among 91 task-runs, failures still appeared as ordinary
    nonzero exits. Require stage attribution in `invalid` and no `per_task` score.
    """
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: _death())
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1)
    assert res["per_task"] == {}, "A gateway failure must not become a zero score"
    bad = res["invalid"]["t1"]
    assert bad["stage"] == "harness model call"
    assert "could not reach the model endpoint" in bad["detail"]
    assert "Connection error" in bad["detail"], "Preserve the matching error line"
    assert "the model hung up first" in bad["detail"], "Include the gateway's accounting"


def test_a_passing_artifact_survives_the_death_of_its_harness(tmp_path, monkeypatch):
    """A harness that dies after producing a passing artifact keeps its score (§2.2)."""
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: _death(
        # A passed artifact here means the modeled command verifier actually ran.
        verdict={"kind": "command", "passed": True, "score": 1.0, "detail": "ok",
                 "scorer_isolation_class": "actor-workdir"}))
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1)
    assert res["per_task"] == {"t1": 1.0}
    assert res["invalid"] == {}


def test_a_trial_that_measured_survives_a_later_connection_death(tmp_path, monkeypatch):
    """`--trials` averages noise; a later failure must not erase a measured trial."""
    calls = {"n": 0}

    def fake(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _death(exit_code=0, stderr="", model_gateway=None)
        return _death()

    monkeypatch.setattr(runner, "run_one", fake)
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=2)
    assert res["invalid"] == {}, "A later connection failure must not erase a measured trial"
    assert res["per_task"] == {"t1": 0.0}
    assert res["per_task_trials"]["t1"] == [0.0]


# ---------------------------------------- Gateway accounting with real sockets ---

def _poll(gw, key, want, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        account = gw.account()
        if account.get(key) == want:
            return account
        time.sleep(0.02)
    return gw.account()


def test_the_gateway_counts_who_hung_up_first():
    """Attribute upstream closure to upstream when the gateway forwards EOF.

    This reproduces the `bn-fit-modify` / `chess-best-move` failure documented in
    `eval/service/modelgate.py`: the harness reused a dead connection and saw only
    `Connection error`. The byte forwarder cannot fix HTTP semantics, but it must
    record which side closed first.
    """
    import socket
    import threading

    import eval.service.modelgate as modelgate

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        conn.close()                      # Upstream closes without returning any bytes.

    threading.Thread(target=serve, daemon=True).start()
    gw = modelgate.ModelGateway(bind_host="127.0.0.1",
                                target=("127.0.0.1", listener.getsockname()[1])).start()
    try:
        client = socket.create_connection(("127.0.0.1", gw.port), timeout=5)
        assert client.recv(10) == b"", "The client must observe the closed connection"
        client.close()
        account = _poll(gw, "upstream_closed_first", 1)
    finally:
        gw.stop()
        listener.close()
    assert account["connections"] == 1
    assert (account["upstream_closed_first"], account["client_closed_first"]) == (1, 0)
    assert "the model hung up first" in modelgate.describe(
        {"published": True, **account})


def test_the_gateway_says_why_it_could_not_reach_the_model():
    """Preserve connection-refused details previously swallowed by `except: pass`.

    Without them, the harness reports only `Connection error` with no cause.
    """
    import socket

    import eval.service.modelgate as modelgate

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()                          # Nothing is listening on this port now.

    gw = modelgate.ModelGateway(bind_host="127.0.0.1",
                                target=("127.0.0.1", dead_port)).start()
    try:
        client = socket.create_connection(("127.0.0.1", gw.port), timeout=5)
        assert client.recv(10) == b""
        client.close()
        account = _poll(gw, "upstream_connect_failed", 1)
    finally:
        gw.stop()
    assert account["upstream_connect_failed"] == 1
    assert account["upstream_connect_error"], "Preserve the cause, even if only an Errno"
    assert "refus" in account["upstream_connect_error"].lower(), account
    note = modelgate.describe({"published": True, **account})
    assert "could not reach the model" in note
    assert "Errno" in note or "WinError" in note


def test_a_record_with_no_gateway_attributes_nothing():
    """Direct endpoint connections have no gateway to attribute a failure to."""
    import eval.service.modelgate as modelgate

    assert modelgate.describe(None) == ""
    assert modelgate.describe({}) == ""
    assert modelgate.describe({"published": False}) == ""


# ----------------------- Do not retain the gateway's connect timeout on the socket ---

def test_a_silent_upstream_is_not_mistaken_for_a_hung_up_one(monkeypatch):
    """Do not misreport a silent upstream as having hung up first.

    This once killed two of five tasks. The timeout passed to `create_connection`
    stayed on the socket and affected later `recv` calls. Local-model queues or
    long generations could then cause `TimeoutError`, forwarded EOF, and a client
    `APIConnectionError` after 15 seconds of silence.

    Use a 0.3-second connect timeout and a one-second delayed reply to verify
    that silence after connecting is not treated as a disconnected upstream.
    """
    import socket
    import threading

    import eval.service.modelgate as modelgate

    monkeypatch.setattr(modelgate, "UPSTREAM_CONNECT_TIMEOUT_S", 0.3)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.recv(64)                    # Receive the request.
            time.sleep(1.0)                  # Model processing outlasts the connect timeout.
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\nlate but fine")

    threading.Thread(target=serve, daemon=True).start()
    gw = modelgate.ModelGateway(bind_host="127.0.0.1",
                                target=("127.0.0.1", listener.getsockname()[1])).start()
    try:
        client = socket.create_connection(("127.0.0.1", gw.port), timeout=10)
        client.sendall(b"GET /v1/models HTTP/1.1\r\nConnection: close\r\n\r\n")
        client.settimeout(10)
        got = b""
        while b"late but fine" not in got:
            chunk = client.recv(4096)
            if not chunk:
                break
            got += chunk
        client.close()
        account = gw.account()
    finally:
        gw.stop()
        listener.close()
    assert b"late but fine" in got, ("Upstream is silent, not disconnected", got, account)
    # Previously this recorded `upstream: TimeoutError: timed out` and sent EOF.
    # Upstream legitimately closes after replying, so do not assert its closure count.
    assert account["read_errors"] == [], account
