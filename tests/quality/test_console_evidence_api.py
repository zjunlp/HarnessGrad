"""Offline console evidence API with synthetic receipt data."""
import http.client
import importlib.util
import json
import threading
from pathlib import Path

from harnessgrad.evidence_export import check_export
from console import evidence_service
from test_evidence_export import author, save_run, SECRET, PRIVATE_TASK

ROOT = Path(__file__).resolve().parents[2]


def test_http_catalog_export_and_guards(tmp_path, monkeypatch):
    root = tmp_path / 'runs'
    save_run(root, 'base')
    save_run(root, 'sub', 1, tree='b')
    spec = importlib.util.spec_from_file_location('evidence_server', ROOT / 'console/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, 'RUNS', root)
    httpd = server.ConsoleServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    def request(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result
    try:
        status, _, raw = request('GET', '/api/evidence/catalog')
        catalog = json.loads(raw)
        assert status == 200 and catalog['scope']['run_count'] == 2
        assert catalog['runs'][0]['rounds'][0]['score'] is not None
        for private in (SECRET, PRIVATE_TASK, '/private/workspace'):
            assert private.encode() not in raw
        body = json.dumps({'manifest': author()})
        status, headers, raw = request('POST', '/api/evidence/export', body, {'Content-Type': 'application/json'})
        assert status == 200 and not check_export(json.loads(raw))
        assert headers['Content-Disposition'] == 'attachment; filename="evidence-export.json"'
        assert request('POST', '/api/evidence/export', json.dumps({'manifest': author(), 'path': '/tmp'}), {'Content-Type': 'application/json'})[0] == 400
        assert request('POST', '/api/evidence/export', '{"manifest":{},"manifest":{}}', {'Content-Type': 'application/json'})[0] == 400
        assert request('GET', '/api/evidence/catalog', headers={'Origin': 'http://evil.invalid'})[0] == 403
        assert request('POST', '/api/evidence/export', '{}', {'Content-Type': 'text/plain'})[0] == 415
        monkeypatch.setitem(evidence_service.LIMITS, 'max_runs', 1)
        assert request('GET', '/api/evidence/catalog')[0] == 413
        monkeypatch.setitem(evidence_service.LIMITS, 'max_runs', 500)
        monkeypatch.setitem(evidence_service.LIMITS, 'max_input_bytes', 1)
        assert request('POST', '/api/evidence/export', body, {'Content-Type': 'application/json'})[0] == 413
        monkeypatch.setitem(evidence_service.LIMITS, 'max_input_bytes', 64 * 1024 * 1024)
        monkeypatch.setitem(evidence_service.LIMITS, 'max_output_bytes', 1)
        assert request('GET', '/api/evidence/catalog')[0] == 413
        evidence_service._GATE.acquire()
        evidence_service._GATE.acquire()
        try:
            assert request('GET', '/api/evidence/catalog')[0] == 429
        finally:
            evidence_service._GATE.release()
            evidence_service._GATE.release()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_receipt_budget_counts_actual_growth_and_recheck(tmp_path, monkeypatch):
    import os
    import pytest
    from harnessgrad import evidence
    from harnessgrad.receipt_verification import ReceiptStore, ReceiptLimitExceeded

    root = tmp_path / 'receipts'
    (root / 'run').mkdir(parents=True)
    path = root / 'run' / 'curve.jsonl'
    path.write_bytes(b'12345')
    for args in ({'max_runs': 0}, {'max_runs': True}, {'max_total_bytes': -1}, {'max_total_bytes': True}):
        with pytest.raises(evidence.EvidenceError):
            ReceiptStore(root, **args)
    with ReceiptStore_context(root, max_total_bytes=9) as store:
        assert store.read('run', 'curve.jsonl') == b'12345'
        with pytest.raises(ReceiptLimitExceeded):
            store.recheck()

    original_fstat = os.fstat
    grown = False
    def grow_after_stat(fd):
        nonlocal grown
        result = original_fstat(fd)
        if not grown and result.st_size == 5:
            grown = True
            with path.open('ab') as stream:
                stream.write(b'67890')
        return result
    with ReceiptStore_context(root, max_total_bytes=6) as store:
        monkeypatch.setattr(os, 'fstat', grow_after_stat)
        with pytest.raises(ReceiptLimitExceeded):
            store.read('run', 'curve.jsonl')
    assert grown


class ReceiptStore_context:
    def __init__(self, *args, **kwargs):
        from harnessgrad.receipt_verification import ReceiptStore
        self.store = ReceiptStore(*args, **kwargs)

    def __enter__(self):
        return self.store

    def __exit__(self, *args):
        self.store.close()


def test_invalid_deployment_limits_report_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_CONSOLE_EVIDENCE_MAX_RUNS', 'invalid')
    spec = importlib.util.spec_from_file_location('invalid_evidence_config', ROOT / 'console/evidence_service.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid configuration must not read receipts')
    monkeypatch.setattr(module, 'ReceiptStore', forbidden)
    assert module.execute(tmp_path) == (503, {'error': 'evidence-unavailable'})
    assert module.execute(tmp_path, {}, is_export=True) == (503, {'error': 'evidence-unavailable'})
