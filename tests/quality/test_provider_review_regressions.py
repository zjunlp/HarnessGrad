"""Offline regressions from independent generic-provider review."""
from types import SimpleNamespace

import pytest

from data.scaffolds import provider_worker as worker, registry
from harnessgrad import dataset_provider as client, dataset_runtime as runtime


@pytest.mark.parametrize("reason", ["reference actions unavailable", True])
def test_native_control_refusal_reaches_client_validation(monkeypatch, reason):
    # Refusal is a supported protocol result; it has no public task or oracle.
    module = SimpleNamespace(command_control=lambda **kwargs: {"refused": reason})
    monkeypatch.setattr(runtime, "descriptor", lambda name: {"provider": "adapter"})
    monkeypatch.setattr(registry, "execution_runtime_name", lambda name: None)
    monkeypatch.setattr(worker.importlib, "import_module", lambda name: module)
    result = worker.dispatch({"dataset": "native", "operation": "control", "task_ids": None})
    provider = object.__new__(client.DatasetProvider)
    provider.name, provider.declaration = "native", {"provider": "adapter"}
    provider._request = lambda *args, **kwargs: result
    if isinstance(reason, str):
        assert provider.control() == {"refused": reason}
    else:
        with pytest.raises(client.DatasetProviderError, match="malformed control snapshot"):
            provider.control()


def test_native_worker_errors_discard_adapter_output_and_private_details(monkeypatch, capsys):
    import io
    import json
    raw = {'schema': worker.SCHEMA, 'dataset': 'native', 'operation': 'control',
           'descriptor_sha256': 'descriptor', 'runtime_id': 'runtime'}
    monkeypatch.setattr(worker, 'sys', SimpleNamespace(
        stdin=io.StringIO(json.dumps(raw)), flags=SimpleNamespace(isolated=True),
        dont_write_bytecode=True, executable='/selected/python', prefix='/selected'))
    monkeypatch.setattr(worker, '_request', lambda value: value)
    monkeypatch.setattr(runtime, 'descriptor', lambda name: {'provider': 'adapter'})
    def dispatch(value):
        print('PRIVATE-WALKTHROUGH-OUTPUT')
        raise ValueError('PRIVATE-CREDENTIAL-ERROR')
    monkeypatch.setattr(worker, 'dispatch', dispatch)
    assert worker.main() == 1
    output = capsys.readouterr()
    response = json.loads(output.out)
    assert response['error'] == {'type': 'ValueError', 'detail': 'adapter operation failed'}
    assert 'PRIVATE' not in output.out + output.err


@pytest.mark.parametrize('field,value', [
    ('oracle_files', {'': 'PRIVATE'}), ('oracle_files', {'/escape': 'PRIVATE'}),
    ('oracle_files', {'../escape': 'PRIVATE'}), ('oracle_files', {'a/../escape': 'PRIVATE'}),
    ('oracle_files', {'./file': 'PRIVATE'}), ('oracle_files', {'a//file': 'PRIVATE'}),
    ('oracle_files', {'a/': 'PRIVATE'}), ('oracle_files', {'.': 'PRIVATE'}),
    ('oracle_files', {'a\\file': 'PRIVATE'}), ('oracle_files', {'a\x00file': 'PRIVATE'}),
    ('noop_expected', -1), ('noop_expected', 10 ** 1000), ('noop_expected', 1.01), ('noop_expected', float('nan')),
    ('noop_expected', float('inf')), ('noop_expected', True),
])
def test_native_control_rejects_unsafe_oracle_and_unmeasured_expectation(field, value):
    provider = object.__new__(client.DatasetProvider)
    provider.name, provider.declaration = 'native', {'provider': 'adapter'}
    result = {'task': {'task_id': 'native/one'}, 'setup': {}, 'verifier': {}, 'env': {},
              'oracle_files': {'.oracle/actions.json': 'PRIVATE'}, 'noop_expected': 0}
    provider._request = lambda *args, **kwargs: result
    assert provider.control()['noop_expected'] == 0
    result[field] = value
    with pytest.raises(client.DatasetProviderError) as caught:
        provider.control()
    assert caught.value.error_type == 'ProtocolError'
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('expected', [0, .5, 1])
def test_native_control_accepts_normalized_oracle_and_measured_expectation(expected):
    provider = object.__new__(client.DatasetProvider)
    provider.name, provider.declaration = 'native', {'provider': 'adapter'}
    result = {'task': {'task_id': 'native/one'}, 'setup': {}, 'verifier': {}, 'env': {},
              'oracle_files': {'.oracle/actions.json': 'PRIVATE'}, 'noop_expected': expected}
    provider._request = lambda *args, **kwargs: result
    assert provider.control() == result


@pytest.mark.parametrize('operation', ['catalog', 'resolve', 'control'])
@pytest.mark.parametrize('key', ['oracle_files', 'walkthrough', 'gold', 'credentials',
                               'configuration', 'HG_AGENT_API_KEY', 'judge_secret', None])
def test_real_worker_rejects_private_public_fields_without_echoing_contents(tmp_path, operation, key):
    """Launch the actual worker under isolated Python; inject only authored adapters."""
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    source = str(Path(worker.__file__).resolve().parents[2])
    script = '''import sys,json
sys.path.insert(0,sys.argv[1])
from types import SimpleNamespace
from data.scaffolds import provider_worker as worker, registry
from harnessgrad import dataset_runtime as runtime
request=json.load(sys.stdin)
task={'task_id':'native/one','goal':'public mentions walkthrough and credentials',
      'inputs':{'account':'public-user','credentials':'public account description'}}
if request['private_key'] is not None:
    task[request['private_key']]='PRIVATE-SENTINEL'
runtime.descriptor=lambda name: {'provider':'adapter'}
registry.execution_runtime_name=lambda name: None
registry.load_split=lambda name: ([task],{'native/one':'PRIVATE-ANSWER'},None)
registry.load_tasks=lambda name,**kwargs: ([task],{}, {})
registry.load_envs=lambda name,**kwargs: {}
module=SimpleNamespace(command_control=lambda **kwargs: {'task':task,'oracle_files':{'actions':'PRIVATE'}})
worker.importlib.import_module=lambda name: module
worker._request=lambda raw: raw
worker.sys.stdin=__import__('io').StringIO(json.dumps(request))
raise SystemExit(worker.main())
'''
    request = {'schema': worker.SCHEMA, 'dataset': 'native', 'operation': operation,
               'task_ids': None, 'private_key': key}
    completed = subprocess.run([sys.executable, '-I', '-B', '-c', script, source],
        input=json.dumps(request), text=True, capture_output=True, cwd=tmp_path,
        env={'PATH': os.defpath, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}, timeout=10)
    response = json.loads(completed.stdout)
    if key is None:
        assert completed.returncode == 0
        result = response['result']
        task = result['task'] if operation == 'control' else result['tasks'][0]
        assert task['inputs']['credentials'] == 'public account description'
    else:
        assert completed.returncode == 1
        assert response['error']['detail'] == 'adapter operation failed'
        assert 'PRIVATE' not in completed.stdout + completed.stderr


@pytest.mark.parametrize('expected', [0, .5, 1, -1, 1.01, 10 ** 1000,
                                      -(10 ** 1000), float('nan'), float('inf'),
                                      float('-inf'), True, None, 'PRIVATE'])
def test_qa_control_enforces_measured_noop_expectation(expected):
    provider = object.__new__(client.DatasetProvider)
    provider.name, provider.declaration = 'qa_control', {'provider': 'qa'}
    result = {'record': {'task_id': 'one', 'gold': {}}, 'task': {'task_id': 'one'},
              'setup': {}, 'verifier': {}, 'env': {}, 'submission': None,
              'prediction': None, 'noop_expected': expected}
    provider._request = lambda *args, **kwargs: result
    if type(expected) in (int, float) and 0 <= expected <= 1:
        assert provider.control('one') == result
    else:
        with pytest.raises(client.DatasetProviderError) as caught:
            provider.control('one')
        assert caught.value.error_type == 'ProtocolError'
        assert 'PRIVATE' not in str(caught.value)
