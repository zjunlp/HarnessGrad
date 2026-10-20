"""Optional runtime limits preserve explicit enforcement and integrity checks."""
import json
import pytest

from eval.service.tool_transport import Relay, call, ToolTransportError
from eval.run.tool_relay import ToolRelay


def test_optional_transport_does_not_add_old_caps(tmp_path):
    payload = 'x' * (8 * 1024 * 1024 + 100)
    with Relay(tmp_path / 'relay.sock', lambda op, args: args,
               max_calls=None, max_line_bytes=None) as relay:
        assert call(relay.path, 'echo', {'text': payload},
                    timeout_s=None, max_line_bytes=None)['text'] == payload
        for _ in range(4100):
            assert call(relay.path, 'echo', {}, timeout_s=None) == {}
        assert relay.receipt['calls'] == 4101
        assert relay.receipt['refused'] == 0


def test_explicit_transport_call_and_reply_limits_still_enforced(tmp_path):
    with Relay(tmp_path / 'r.sock', lambda *_: 'x' * 1024,
               max_calls=1, max_line_bytes=100) as relay:
        with pytest.raises(ToolTransportError, match='reply_too_large'):
            call(relay.path, 'test', {}, timeout_s=None)
        with pytest.raises(ToolTransportError, match='call_budget'):
            call(relay.path, 'test', {}, timeout_s=None)
        assert relay.receipt['refused'] == 2


def recorded_handle(tmp_path, policy):
    handle = ToolRelay(tmp_path / 'socket', tmp_path, None,
                       tmp_path / 'receipt', tmp_path / 'log')
    handle.stopped = True
    handle.record_spec = policy
    handle.context = {'task_id': 'fixture/one', 'run_id': 'run', 'trial_id': 'one'}
    return handle


def test_record_size_optional_but_integrity_required(tmp_path):
    source = tmp_path / 'episode.json'
    source.write_text(json.dumps({'value': 'x' * (32 * 1024 * 1024 + 1)}))
    handle = recorded_handle(tmp_path, {'path': 'trusted', 'max_bytes': None})
    result = handle.recording_input()
    assert result is not None
    assert (tmp_path / 'sealed' / 'episode.json').stat().st_size > 32 * 1024 * 1024


def test_explicit_record_size_and_file_type_still_enforced(tmp_path):
    source = tmp_path / 'episode.json'
    source.write_text('{"value": "long"}')
    handle = recorded_handle(tmp_path, {'path': 'trusted', 'max_bytes': 2})
    with pytest.raises(RuntimeError, match='exceeds max_bytes'):
        handle.recording_input()
    source.unlink()
    (tmp_path / 'target').write_text('{}')
    source.symlink_to(tmp_path / 'target')
    handle.record_spec['max_bytes'] = None
    with pytest.raises(OSError):
        handle.recording_input()
