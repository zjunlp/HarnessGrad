"""Real subprocess errors survive relay cleanup without entering public feedback."""
import hashlib
import json
from pathlib import Path
import socket
import sys

import pytest

from eval.run import tool_relay
from eval.service import tool_transport


ROOT = Path(__file__).resolve().parents[2]
CONTEXT = {'task_id': 'fixture/one', 'run_id': 'diagnostics', 'trial_id': 'one', 'seed': '42'}
PROGRAM = r'''
import argparse, json, os, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, REPO)
from eval.service.tool_transport import Relay
parser = argparse.ArgumentParser()
parser.add_argument('--socket'); parser.add_argument('--receipt'); parser.add_argument('--game')
args = parser.parse_args()
mode = Path(args.game).read_text()
if mode == 'startup':
    raise RuntimeError('private-startup-detail')
if mode == 'timeout':
    time.sleep(30)
done = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: done.set())
original_loads = json.loads
def loads(raw, *a, **k):
    if isinstance(raw, bytes) and b'"op": "parser"' in raw:
        raise MemoryError('private-parser-detail')
    return original_loads(raw, *a, **k)
json.loads = loads
def handle(op, arguments):
    if op == 'handler':
        raise ValueError('private-handler-detail')
    if op == 'serialization':
        cyclic = []
        cyclic.append(cyclic)
        return cyclic
    if op == 'disconnect':
        time.sleep(.05)
    return {'alive': True}
with Relay(args.socket, handle) as relay:
    done.wait()
failed = relay.receipt['errors'] > relay.receipt['request_errors']
Path(args.receipt).write_text(json.dumps({'transport': relay.receipt,
    'failure_detail': {'type': 'FixtureError', 'message': 'private-receipt-detail',
                       'traceback': 'private-receipt-trace'}}))
Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps({'private': 'private-episode-detail'}))
raise SystemExit(int(failed))
'''


@pytest.fixture
def setup(tmp_path):
    script = tmp_path / 'relay.py'
    script.write_text(PROGRAM.replace('REPO', repr(str(ROOT))))
    return {'tool_relay': {'program': str(script), 'python': sys.executable,
                          'content': 'normal', 'record': {'path': 'trusted_episode'}}}


@pytest.mark.parametrize('operation,category,error_type', [
    ('handler', 'handler', 'ValueError'),
    ('serialization', 'serialization', 'ValueError'),
    ('parser', 'parser', 'MemoryError'),
])
def test_original_error_is_flushed_and_private_evidence_survives_cleanup(setup, operation, category, error_type):
    relay = tool_relay.start(setup, context=CONTEXT, private_diagnostics=True)
    try:
        with pytest.raises(tool_transport.ToolTransportError):
            tool_transport.call(relay.path, operation, {'secret': 'request-body-must-not-be-logged'})
        # Log is present before SIGTERM, receipt creation, or snapshot collection.
        events = [json.loads(line) for line in relay.log_path.read_text().splitlines()]
        assert events[0]['category'] == category and events[0]['type'] == error_type
        assert events[0]['message'] and 'Traceback' in events[0]['traceback']
        assert 'request-body-must-not-be-logged' not in relay.log_path.read_text()
        relay.cleanup()
        assert not relay.directory.exists()
        assert relay.fault() == 'relay exited 1'
        full = relay.diagnostic
        assert full['binding']['task_id'] == CONTEXT['task_id']
        assert full['stage'] == 'runtime' and full['scorable'] is False
        assert 'private-episode-detail' in full['files']['episode.json']['content']
        assert 'private-receipt-detail' in full['files']['receipt.json']['content']
        for entry in full['files'].values():
            assert entry['state'] == 'captured'
            assert entry['sha256'] == hashlib.sha256(entry['content'].encode()).hexdigest()
        public = json.dumps(relay.report())
        assert 'content' not in public and 'Traceback' not in public and 'private-' not in public
        assert relay.report()['diagnostic']['files']['relay.log']['sha256']
        assert relay.capture_diagnostic() is full
        with pytest.raises(RuntimeError, match='failed relay'):
            relay.recording_input()
    finally:
        relay.cleanup()


def test_broken_client_transport_is_logged_without_request_contents(setup):
    relay = tool_relay.start(setup, context=CONTEXT, private_diagnostics=True)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(relay.path))
            client.sendall(b'{"op":"disconnect","args":{"secret":"request-secret"}}\n')
            client.shutdown(socket.SHUT_RDWR)
        assert tool_transport.call(relay.path, 'ok') == {'alive': True}
        relay.cleanup()
        events = [json.loads(line) for line in relay.diagnostic['files']['relay.log']['content'].splitlines()]
        assert any(event['category'] == 'transport' and event['type'] == 'BrokenPipeError' for event in events)
        assert 'request-secret' not in relay.diagnostic['files']['relay.log']['content']
    finally:
        relay.cleanup()


@pytest.mark.parametrize('mode', ['startup', 'timeout', 'missing_python'])
def test_startup_failure_carries_evidence_after_private_cleanup(setup, mode):
    setup['tool_relay']['content'] = mode
    if mode == 'missing_python':
        setup['tool_relay']['python'] = '/nonexistent/hg-diagnostic-python'
    with pytest.raises(tool_relay.ToolRelayStartError) as caught:
        tool_relay.start(setup, context=CONTEXT, ready_timeout_s=.2, private_diagnostics=True)
    exc = caught.value
    assert exc.report['cleanup'] == {'complete': True, 'errors': []}
    assert exc.diagnostic['stage'] == 'startup'
    assert exc.diagnostic['binding']['task_id'] == CONTEXT['task_id']
    assert exc.diagnostic['files']['receipt.json']['state'] == 'missing'
    assert exc.diagnostic['files']['episode.json']['state'] == 'missing'
    assert 'private-startup-detail' not in json.dumps(exc.report) + exc.safe_message
    if mode == 'startup':
        assert 'private-startup-detail' in str(exc)
        assert 'RuntimeError: private-startup-detail' in exc.diagnostic['files']['relay.log']['content']
    elif mode == 'missing_python':
        assert exc.diagnostic['exception']['type'] == 'FileNotFoundError'
        assert 'hg-diagnostic-python' in exc.diagnostic['exception']['message']


def test_truncation_missing_and_unsafe_sources_are_explicit(setup, monkeypatch, tmp_path):
    relay = tool_relay.start(setup, context=CONTEXT, private_diagnostics=True)
    try:
        relay.stop()
        raw = b'old-data-' * 10 + b'latest-error'
        relay.log_path.write_bytes(raw)
        relay.receipt_path.unlink()
        episode = relay.directory / 'episode.json'
        episode.unlink()
        private = tmp_path / 'private-target'
        private.write_text('must-not-follow-diagnostic-symlink')
        episode.symlink_to(private)
        monkeypatch.setattr(tool_relay, 'DIAGNOSTIC_FILE_BYTES', 16)
        relay.cleanup()
        files = relay.diagnostic['files']
        assert files['relay.log']['state'] == 'truncated'
        assert files['relay.log']['bytes'] == len(raw)
        assert files['relay.log']['captured_bytes'] == 16
        assert files['relay.log']['offset'] == len(raw) - 16
        assert files['relay.log']['content'].encode() == raw[-16:]
        assert files['relay.log']['sha256'] == hashlib.sha256(raw[-16:]).hexdigest()
        assert files['receipt.json']['state'] == 'missing'
        assert files['episode.json']['state'] == 'unreadable'
        assert 'must-not-follow' not in json.dumps(relay.diagnostic)
    finally:
        relay.cleanup()


def test_capture_and_cleanup_errors_remain_secondary(setup, monkeypatch):
    relay = tool_relay.start(setup, context=CONTEXT, private_diagnostics=True)
    try:
        with pytest.raises(tool_transport.ToolTransportError):
            tool_transport.call(relay.path, 'handler')
        relay.stop()
        def failed_capture(*args, **kwargs):
            raise OSError('private capture error')
        def failed_cleanup(*args, **kwargs):
            raise PermissionError('fixture cleanup failure')
        with monkeypatch.context() as patch:
            patch.setattr(tool_relay, '_snapshot', failed_capture)
            patch.setattr(tool_relay.shutil, 'rmtree', failed_cleanup)
            relay.cleanup()
        assert relay.fault() == 'relay exited 1'
        assert relay.diagnostic['errors'] == [{'type': 'OSError'}]
        assert relay.report()['cleanup']['errors']
        assert 'private capture error' not in json.dumps(relay.report())
        relay.cleanup()
        assert not relay.directory.exists()
    finally:
        relay.cleanup()


def test_diagnostic_limits_do_not_hide_first_handler_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool_transport, 'MAX_DIAGNOSTIC_EVENTS', 1)
    monkeypatch.setattr(tool_transport, 'MAX_DIAGNOSTIC_MESSAGE', 8)
    monkeypatch.setattr(tool_transport, 'MAX_DIAGNOSTIC_TRACEBACK', 32)
    def failed_handler(*_):
        raise ValueError('original-private-failure')
    relay = tool_transport.Relay(tmp_path / 'unused.sock', failed_handler)
    assert relay._handle(b'[]')['error'] == 'bad_request'
    assert relay._handle(b'[]')['error'] == 'bad_request'
    reply = relay._handle(b'{"op":"call"}')
    assert reply == {'ok': False, 'error': 'ValueError: original-private-failure'}
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert len(events) == 2 and events[-1]['category'] == 'handler'
    assert events[-1]['truncated'] is True
    assert relay.receipt['diagnostics']['dropped'] == 1
    assert relay.receipt['diagnostics']['first_failure']['type'] == 'ValueError'
    assert 'original-private' not in json.dumps(relay.receipt)


@pytest.mark.parametrize('failed', [False, True])
def test_legacy_receipt_and_failure_visibility_are_unchanged(setup, failed):
    relay = tool_relay.start(setup, context=CONTEXT)
    try:
        if failed:
            with pytest.raises(tool_transport.ToolTransportError):
                tool_transport.call(relay.path, 'handler')
        relay.cleanup()
        assert relay.report()['receipt']['failure_detail']['message'] == 'private-receipt-detail'
        if failed:
            assert 'private-handler-detail' in relay.fault()
            assert relay.diagnostic['files']['relay.log']['state'] == 'captured'
        else:
            assert relay.fault() is None and relay.diagnostic is None
    finally:
        relay.cleanup()


def test_accepted_record_uses_existing_reference_instead_of_duplicate_snapshot(setup, tmp_path):
    relay = tool_relay.start(setup, context=CONTEXT, private_diagnostics=True)
    try:
        relay.stop()
        relay.recording_input(archive_root=tmp_path / 'recordings')
        relay.cleanup()
        episode = relay.diagnostic['files']['episode.json']
        assert episode == {'state': 'not_needed', 'recording': relay.recording}
        assert Path(episode['recording']['archive']).is_file()
    finally:
        relay.cleanup()
