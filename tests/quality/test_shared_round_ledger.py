"""Offline process and fault tests for the cooperative candidate ledger."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'console' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def update(root, start, prefix):
    ledger = module('round_ledger')
    start.wait()
    for i in range(15):
        with ledger.locked(root) as txn:
            obj = txn.read()
            obj[f'{prefix}-{i}'] = {'n': i}
            txn.write(obj)


def publish(root, start):
    publisher = module('round_publish')
    with publisher.Publisher(root) as p:
        with p.stage() as stage:
            start.wait()
            p.publish(stage, 'published', {'from': 'publisher'})


def test_process_writers_and_publisher_cooperate(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    start = ctx.Event()
    processes = [ctx.Process(target=update, args=(tmp_path, start, str(i))) for i in range(3)]
    processes.append(ctx.Process(target=publish, args=(tmp_path, start)))
    for proc in processes:
        proc.start()
    start.set()
    for proc in processes:
        proc.join(30)
        assert not proc.is_alive()
        assert proc.exitcode == 0
    obj = json.loads((tmp_path / '.sources.json').read_text())
    assert len(obj) == 46
    assert obj['published'] == {'from': 'publisher'}
    assert all(obj[f'{p}-{i}'] == {'n': i} for p in range(3) for i in range(15))


@pytest.mark.parametrize('contents', [b'{', b'{"a":1,"a":2}', b'[]', b'{"__round_publish_pending__":{}}', b'{"a":NaN}'])
def test_bad_ledger_fails_closed(tmp_path, contents):
    ledger = module('round_ledger')
    path = tmp_path / '.sources.json'
    path.write_bytes(contents)
    with ledger.locked(tmp_path) as txn:
        with pytest.raises(ledger.LedgerError):
            txn.read()
    assert path.read_bytes() == contents


def test_write_failure_preserves_prior_ledger(tmp_path, monkeypatch):
    ledger = module('round_ledger')
    path = tmp_path / '.sources.json'
    path.write_text('{"old":1}')
    def fail(*args, **kwargs):
        raise OSError('injected replace failure')
    monkeypatch.setattr(os, 'replace', fail)
    with ledger.locked(tmp_path) as txn:
        obj = txn.read()
        obj['new'] = 2
        with pytest.raises(OSError):
            txn.write(obj)
    assert json.loads(path.read_text()) == {'old': 1}
    assert not list(tmp_path.glob('.sources-tmp-*'))


def test_symlink_lock_and_ledger_rejected(tmp_path):
    ledger = module('round_ledger')
    victim = tmp_path / 'victim'
    victim.write_text('{}')
    (tmp_path / '.sources.lock').symlink_to(victim)
    with pytest.raises(OSError):
        with ledger.locked(tmp_path):
            pass
    (tmp_path / '.sources.lock').unlink()
    (tmp_path / '.sources.json').symlink_to(victim)
    with ledger.locked(tmp_path) as txn:
        with pytest.raises(OSError):
            txn.read()
    assert victim.read_text() == '{}'


@pytest.mark.parametrize("contents", [b"{", b'{"__round_publish_pending__":{}}'])
def test_http_candidate_list_reports_bad_ledger_without_changing_it(tmp_path, monkeypatch, contents):
    import http.client
    import threading
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    ledger_path = candidates / ".sources.json"
    ledger_path.write_bytes(contents)
    monkeypatch.setenv("HG_CONSOLE_CANDIDATES", str(candidates))
    runs = tmp_path / "runs"
    state = runs / "fixture" / "state_after_round1"
    state.mkdir(parents=True)
    (state / "harness.json").write_text(json.dumps({"name": "test", "version": "1", "entrypoint": "agent.py"}))
    (state / "agent.py").write_text("pass\n")
    monkeypatch.setenv("HG_CONSOLE_RUNS", str(runs))
    spec = importlib.util.spec_from_file_location("ledger_http_server", ROOT / "console" / "server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    httpd = server.ConsoleServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        conn.request("GET", "/api/harness/candidates")
        response = conn.getresponse()
        body = json.loads(response.read())
        assert response.status == 500
        assert "ledger" in body["error"]
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        data = json.dumps({"run": "fixture", "round": 1})
        conn.request("POST", "/api/harness/from-round", body=data,
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        body = json.loads(response.read())
        assert response.status == 409
        assert body["error"] == "unsafe-recovery"
        conn.close()
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()
    assert ledger_path.read_bytes() == contents
