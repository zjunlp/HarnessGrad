"""Offline synthetic regressions for the history resource boundary."""
import http.client
import importlib.util
import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def console(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("history_bound_server", ROOT / "console/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = tmp_path / "run"
    (run / "diffs").mkdir(parents=True)
    (run / "diffs" / "round-2.patch").write_text("a patch", encoding="utf-8")
    (run / "diffs" / "round-2.json").write_text(json.dumps({"files": [{"path": "x", "added": 2, "removed": 1}]}))
    calls = []
    monkeypatch.setattr(module, "find_run", lambda name: calls.append(name) or run)
    return module, calls


def test_helper_valid_sparse_and_exact_boundary(console):
    module, calls = console
    result = module.run_history("sample", 999, 0)
    assert (result["from"], result["to"], len(result["rounds"])) == (1, 999, 999)
    assert result["rounds"][0] == {"round": 1, "files": [], "added": 0, "removed": 0, "patch": "", "stored": False}
    assert result["rounds"][1] == {"round": 2, "files": ["x"], "added": 2, "removed": 1, "patch": "a patch", "stored": True}
    assert len(module.run_history("sample", 1000, 1)["rounds"]) == 1000
    assert module.run_history("sample", 0, 0)["rounds"] == []
    assert module.run_history("sample", 2, 2)["rounds"][0]["round"] == 2
    assert len(calls) == 4


@pytest.mark.parametrize("since,upto", [(0, 1000), (1, 999999999), (3, 2), (-1, 2), (1, -1), (1.0, 2), (1, '2'), (True, 2), (1, 10**100)])
def test_helper_rejects_before_lookup_or_filesystem(console, monkeypatch, since, upto):
    module, calls = console
    monkeypatch.setattr(module, "read_json", lambda *args: pytest.fail("unexpected filesystem read"))
    monkeypatch.setattr(Path, "is_file", lambda *args: pytest.fail("unexpected filesystem stat"))
    result = module.run_history("sample", upto, since)
    assert result["error"] == "bad-range"
    assert "rounds" not in result
    assert calls == []


def test_http_ranges(console):
    module, calls = console
    server = module.ConsoleServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(query):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            conn.request("GET", "/api/runs/sample/history?" + query)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()
    try:
        for query in ("from=0&to=1000", "from=1&to=999999999", "from=3&to=2", "from=-1&to=2", "from=1&to=1.5", "from=1&to=1000000000", "from=&to=2", "from=1&to="):
            status, body = request(query)
            assert status == 400 and "error" in body and "rounds" not in body
        assert calls == []
        for query, count in (("from=0&to=999", 999), ("from=1&to=1000", 1000), ("from=0&to=0", 0), ("from=2&to=2", 1), ("to=2", 2)):
            status, body = request(query)
            assert status == 200 and len(body["rounds"]) == count
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
