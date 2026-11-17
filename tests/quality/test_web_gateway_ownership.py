"""The canonical Web gateway and retained CLI expose one shared implementation."""
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_import_shares_class_and_patch_targets(monkeypatch):
    old = importlib.import_module('tools.web_gateway')
    current = importlib.import_module('eval.service.web_gateway')
    assert old is current and old.WebGateway is current.WebGateway
    marker = object()
    monkeypatch.setattr(old, '_fetch_url', marker)
    assert current.WebGateway.fetch_request.__globals__['_fetch_url'] is marker


def test_path_loaded_wrapper_and_outside_checkout_help(tmp_path):
    path = ROOT / 'tools/web_gateway.py'
    spec = importlib.util.spec_from_file_location('_authored_web_gateway', path)
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    assert wrapper.WebGateway is importlib.import_module('eval.service.web_gateway').WebGateway
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr


def test_native_research_relay_imports_gateway_under_isolated_python(tmp_path):
    env = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'data/scaffolds/native_research/web_relay.py'), '--help'],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0 and 'usage:' in result.stdout.lower(), result.stderr


def test_relay_source_change_invalidates_historical_verification_context():
    import json
    from harnessgrad.provenance import verification_context
    fixture = ROOT / 'tests/fixtures/native_web_relay_migration/before.json'
    old = json.loads(fixture.read_text())
    assert verification_context.validate(old) == old
    current = verification_context.capture(
        [{'task_id': 'authored/web', 'goal': 'Authored task'}], {},
        setups={'authored/web': {'tool_relay': {'program': 'data/scaffolds/native_research/web_relay.py'}}})
    assert verification_context.problems(old, current) == ['verification_context changed']


def test_native_relay_serves_authored_local_search_and_stops_cleanly(tmp_path):
    import json
    import socket
    import time
    from eval.service.tool_transport import call
    case = tmp_path / 'case.json'
    case.write_text(json.dumps({'task_id': 'authored/web', 'dataset': 'synthetic'}))
    sock, receipt, record = tmp_path / 'relay.sock', tmp_path / 'receipt.json', tmp_path / 'record.json'
    env = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1', 'HG_WEB_SEARCH': 'none',
           'HG_WEB_FETCH': 'http', 'HG_RELAY_RECORD_PATH': str(record)}
    proc = subprocess.Popen([sys.executable, '-I', '-B', str(ROOT / 'data/scaffolds/native_research/web_relay.py'),
        '--case-spec', str(case), '--socket', str(sock), '--receipt', str(receipt), '--deadline-s', '10'],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        ready = False
        while proc.poll() is None and time.monotonic() < deadline:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.connect(str(sock))
                ready = True
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(.02)
        assert ready, 'authored relay did not become ready'
        assert call(str(sock), 'search', {'query': 'authored query', 'num': 1}) == {'results': []}
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, (stdout, stderr)
        result = json.loads(receipt.read_text())
        assert result['stopped_cleanly'] is True and result['failure'] is None
        assert result['task_id'] == 'authored/web'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
