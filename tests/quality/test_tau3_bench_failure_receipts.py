"""τ³ shutdown preserves known failures in receipts without the official runtime."""
import hashlib
import importlib.util
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data.datasets import tau3_bench as dataset
from eval.service.tool_transport import ToolTransportError, call


@pytest.mark.parametrize('scenario,clean,failure,errors', [
    pytest.param('runtime', True, 'RuntimeError', 2, id='first-error'),
    pytest.param('runtime', False, 'RuntimeError', 2, id='first-error-then-deadline'),
    pytest.param('transport', True, 'transport_failure', 1, id='transport-fallback'),
    pytest.param('deadline', False, 'service_deadline', 0, id='deadline-fallback'),
])
def test_failure_receipt(tmp_path, monkeypatch, scenario, clean, failure, errors):
    # Keep the real adapter entrypoint and socket transport; inject runtime failures
    # and the stop event so the deadline case needs neither a model nor a real wait.
    monkeypatch.syspath_prepend(str(dataset.ASSETS / 'lib'))
    spec = importlib.util.spec_from_file_location('tau3_failure_relay', dataset.ASSETS / 'relay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'bootstrap', lambda bundle: None)

    state = Mock(termination_reason=None, bootstrap_complete=True)
    state.episode.return_value = {}
    if scenario == 'runtime':
        state.start_conversation.side_effect = RuntimeError('first fixture failure')
        state.get_runtime_status.side_effect = ValueError('later fixture failure')
    elif scenario == 'transport':
        state.get_runtime_status.return_value = object()
    monkeypatch.setitem(sys.modules, 'runtime', SimpleNamespace(Tau3Runtime=lambda path: state))
    monkeypatch.setitem(sys.modules, 'model', SimpleNamespace(install=lambda *args: None))

    case = tmp_path / 'case.json'
    task_id = 'tau3_bench/fixture/0'
    case.write_text(json.dumps({'task_id': task_id, 'revision': 'fixture'}))
    case_spec = tmp_path / 'case-spec.json'
    case_spec.write_text(json.dumps({'case': str(case),
        'sha256': hashlib.sha256(case.read_bytes()).hexdigest(),
        'user_model': 'fixture', 'bundle': str(tmp_path / 'bundle')}))
    context = tmp_path / 'context.json'
    context.write_text(json.dumps({'task_id': task_id, 'seed': '123'}))
    episode = tmp_path / 'episode.json'
    receipt = tmp_path / 'receipt.json'
    socket = tmp_path / 'relay.sock'
    monkeypatch.setenv('HG_RELAY_CONTEXT_PATH', str(context))
    monkeypatch.setenv('HG_RELAY_RECORD_PATH', str(episode))
    monkeypatch.setattr(sys, 'argv', ['relay.py', '--case-spec', str(case_spec),
                                    '--socket', str(socket), '--receipt', str(receipt)])

    def wait(deadline):
        if scenario == 'runtime':
            for op, error in [('start', 'RuntimeError'), ('status', 'ValueError')]:
                with pytest.raises(ToolTransportError, match=error):
                    call(socket, op)
        elif scenario == 'transport':
            with pytest.raises(ToolTransportError, match='result_not_serialisable'):
                call(socket, 'status')
        return clean

    stop = SimpleNamespace(wait=wait, set=lambda: None)
    monkeypatch.setattr(module, 'threading', SimpleNamespace(Event=lambda: stop, Lock=threading.Lock))
    monkeypatch.setattr(module, 'signal', SimpleNamespace(
        SIGTERM=module.signal.SIGTERM, SIGINT=module.signal.SIGINT, signal=lambda *args: None))

    assert module.main() == 1
    assert json.loads(episode.read_text())['failure'] == failure
    recorded = json.loads(receipt.read_text())
    assert recorded['failure'] == failure
    assert recorded['task_id'] == task_id
    assert recorded['stopped_cleanly'] is clean
    assert recorded['transport']['errors'] == errors
    assert recorded['transport']['request_errors'] == recorded['transport']['refused'] == 0
    assert not socket.exists()
