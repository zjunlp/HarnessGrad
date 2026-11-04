"""Offline assistant batches against the actual pinned v0.1.0 orchestrator."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_tau2_bench_limits import LIB, ROOT, _PROBE, _available

# Reuse the existing release bootstrap and deterministic participants, not its
# assertions or a reimplementation of official step/budget logic.
_SETUP = _PROBE.split('rows = []')[0].replace(
    "            assert len(message.tool_calls) == 1  # candidate API currently exposes singles\n"
    "            call = message.tool_calls[0]\n"
    "            private.call_assistant_tool(call.name, call.arguments)",
    "            private.call_assistant_tools([{'id': c.id, 'name': c.name, "
    "'arguments': c.arguments} for c in message.tool_calls])",
)
_BATCH_PROBE = _SETUP + r'''
rows = []
for budget in range(0, 8):
    for errors in (0, 1, 10):
        rows.append(compare('assistant_batch', budget, errors,
                            [user(), user('###STOP###')], [calls(batch=True), say()]))

# Validate the entire envelope before even the assistant message/sync step.
ScriptedUser.script = [user()]
with tempfile.TemporaryDirectory(prefix='tau2-batch-') as tmp:
    path = Path(tmp) / 'config.json'
    path.write_text(json.dumps({'domain': 'airline', 'task': {'id': 'fixture',
        'user_scenario': {'instructions': 'fixture'}}, 'seed': 7,
        'max_steps': 100, 'max_errors': 10, 'user_model': 'unused'}))
    private = runtime.Tau2Runtime(path)
private.start_conversation()
valid = {'id': 'a', 'name': 'ok', 'arguments': {}}
invalid = [None, {}, [], [valid, valid], [valid, {'id': '', 'name': 'ok', 'arguments': {}}],
    [valid, {'id': 'b', 'name': 'ok', 'arguments': []}],
    [valid, {'id': 'b', 'name': '', 'arguments': {}}],
    [valid, {'id': 'b', 'name': 'ok', 'arguments': {}, 'requestor': 'user'}],
    [valid, {'id': 'b', 'name': 'ok', 'arguments': {}, 'requestor': 'assistant'}],
    [valid, {'id': 1, 'name': 'ok', 'arguments': {}}], [valid, 'bad']]
# Exercise the real relay boundary as well, with no socket or model calls.
sys.path.insert(0, str(Path(sys.argv[1]).parents[3]))
import importlib.util
spec = importlib.util.spec_from_file_location('batch_relay', Path(sys.argv[1]).parent / 'relay.py')
relay = importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)
service = relay.Service({}, private)
def snapshot():
    return (private.episode(), private.environment.calls[:], private.environment.syncs,
            private._tool_call_counter, private.last_agent_observation)
for payload in invalid:
    before = snapshot()
    try:
        private.call_assistant_tools(payload)
    except ValueError:
        pass
    else:
        raise AssertionError(('accepted invalid batch', payload))
    assert snapshot() == before
    assert service.handle('calls', {'calls': payload}) == {'error': 'invalid_tool_call'}
    assert service.failure is None and snapshot() == before
before = snapshot()
try:
    private.call_assistant_tool('', {})
except ValueError:
    pass
else:
    raise AssertionError('accepted empty single tool name')
assert snapshot() == before
assert service.handle('calls', {'calls': [valid, {'id': 'b', 'name': 'bad', 'arguments': {}}]}) == {'result': 'ok\n\nbad'}
assert private.step_count == 3
assert private.tool_error_count == 1 and private.num_errors == 0
assert [m.id for m in private.messages[-2:]] == ['a', 'b']
assert all(m.requestor == 'assistant' for m in private.messages[-2:])
private.call_assistant_tool('ok', {})
assert private.step_count == 5
assert service.handle('calls', {'calls': [valid], 'requestor': 'user'}) == {'error': 'unsupported_operation_or_fields'}
print(json.dumps({'rows': rows, 'invalid_cases': len(invalid)}))
'''


def test_official_assistant_batch_limits_and_atomic_validation():
    available, reason = _available()
    if reason:
        pytest.skip(reason)
    python, bundle = available
    proc = subprocess.run([str(python), '-I', '-c', _BATCH_PROBE, str(LIB), str(bundle)],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert proc.returncode == 0, proc.stderr[-6000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(result['rows']) == 24
    rows = [r for r in result['rows'] if r['max_errors'] == 10]
    assert next(r for r in rows if r['budget'] == 2)['tool_count'] == 0
    assert next(r for r in rows if r['budget'] == 3)['tool_count'] == 2
    assert result['invalid_cases'] == 11


def test_calls_cli_forwards_json_list(monkeypatch, capsys):
    import types
    monkeypatch.setitem(sys.modules, 'tau2_transport', types.SimpleNamespace(call=None))
    sys.path.insert(0, str(ROOT / 'data/datasets/tau2_bench/cli'))
    spec = importlib.util.spec_from_file_location('batch_cli', ROOT / 'data/datasets/tau2_bench/cli/tau2.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    calls = [{'id': 'one', 'name': 'lookup', 'arguments': {'x': 1}},
             {'id': 'two', 'name': 'lookup', 'arguments': {'x': 2}}]
    recorded = []
    monkeypatch.setattr(cli, 'call', lambda *a, **kw: recorded.append((a, kw)) or {'result': 'ok'})
    monkeypatch.setattr(sys, 'argv', ['tau2.py', 'calls', json.dumps(calls)])
    cli.main()
    assert recorded[0][0] == ('/run/harnessgrad-tool/tool.sock', 'calls', {'calls': calls})
    assert json.loads(capsys.readouterr().out) == {'result': 'ok'}
