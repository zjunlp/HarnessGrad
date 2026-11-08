"""Managed dependency/source controls; all native scoring is synthetic."""
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

from data.datasets import agentdojo as adapter
from harnessgrad import dataset_runtime


def bootstrap_module():
    spec = importlib.util.spec_from_file_location('dojo_bootstrap_control', adapter.ASSETS / 'bootstrap.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_lock_keeps_dependency_pins_and_separate_source():
    descriptor = dataset_runtime.execution_descriptor('agentdojo')
    assert descriptor['python'] == '3.12'
    assert descriptor['pins']['pydantic'] == '2.13.5'
    assert descriptor['pins']['openai'] == '3.26.0'
    assert 'agentdojo' not in descriptor['pins']


def test_source_manifest_change_is_refused_before_import(tmp_path):
    module = bootstrap_module()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{"files":{}}')
    module.MANIFEST = manifest
    with pytest.raises(ValueError, match='manifest digest mismatch'):
        module.validate_source(tmp_path)


def test_missing_extra_or_changed_source_is_refused(tmp_path):
    source = tmp_path / 'agentdojo'
    source.mkdir()
    (source / 'private.py').write_text('PRIVATE-GOLD-SENTINEL')
    with pytest.raises(ValueError, match='source inventory mismatch') as error:
        bootstrap_module().validate_source(tmp_path)
    assert 'PRIVATE-GOLD-SENTINEL' not in str(error.value)


@pytest.fixture
def native(tmp_path, monkeypatch):
    store = os.environ.get('HG_AGENTDOJO_EXECUTION_TEST_STORE')
    source = os.environ.get('HG_AGENTDOJO_EXECUTION_TEST_SOURCE')
    if not store or not source:
        pytest.skip('explicit prepared external store/source are required')
    runtime = dataset_runtime.resolve_execution('agentdojo', root=store)
    bootstrap_module().validate_source(Path(source))
    monkeypatch.setattr(adapter, '_execution_runtime', lambda: runtime)
    monkeypatch.setattr(adapter, '_source_root', lambda: Path(source))
    monkeypatch.setattr(adapter, 'DATASET_ROOT', tmp_path)
    folder = tmp_path / adapter.PREPARED / '0'
    folder.mkdir(parents=True)
    (folder / 'case.json').write_text('{"private":"PRIVATE-GOLD-SENTINEL"}')
    return runtime


@pytest.mark.parametrize('output,expected', [('synthetic-correct', 1), ('synthetic-wrong', 0)])
def test_native_scorer_wrapper_uses_selected_readonly_runtime(native, tmp_path, monkeypatch, output, expected):
    from eval.verification.verifier import _verify
    spec = adapter.VERIFY['agentdojo/0']
    # Exercise the real wrapper/bootstrap and source admission, with a synthetic
    # evaluate API. No official task, model, or benchmark episode is executed.
    bridge = '''import os,sys
from pathlib import Path
def evaluate(case, action):
    return {'reward': float(action['output'] == 'synthetic-correct'),
            'utility': action['output'] == 'synthetic-correct', 'attack_success': False,
            'suite_version': 'synthetic', 'python': sys.executable,
            'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY)}
'''
    code = tmp_path / 'synthetic-code'
    (code / 'verify').mkdir(parents=True)
    for name in ('bootstrap.py', 'source-manifest.json', 'verify/check.py'):
        shutil.copyfile(adapter.ASSETS / name, code / name)
    (code / 'bridge.py').write_text(bridge)
    for item in spec['inputs']:
        if item['path'] == 'dojo_code':
            item['from'] = str(code)
    work = tmp_path / ('work-' + str(expected))
    work.mkdir()
    (work / 'action.json').write_text(json.dumps({'output': output}))
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    result = _verify(work, spec, '', sandbox=True, timeout_s=30, env={'kind': 'files'})
    assert result.get('score') == expected, json.dumps(result)
    assert result['evidence']['python'] == native['python']
    assert result['evidence']['readonly']
    assert 'PRIVATE-GOLD-SENTINEL' not in json.dumps(result)


def test_native_synthetic_service_admits_selected_runtime_and_source(native, tmp_path):
    from eval.run import tool_relay
    from eval.service.tool_transport import call
    setup = adapter.SETUP['agentdojo/0']
    program = tmp_path / 'synthetic-service.py'
    program.write_text('''import argparse,json,os,signal,sys,threading
from pathlib import Path
sys.path.insert(0, ROOT)
sys.path.insert(0, ASSETS)
from bootstrap import bootstrap
from eval.service.tool_transport import Relay
p=argparse.ArgumentParser()
p.add_argument('--case');p.add_argument('--socket');p.add_argument('--receipt')
a=p.parse_args()
config=json.loads(Path(a.case).read_text())
identity=bootstrap(Path(config['source_root']))
import bridge
assert bridge.VERSION == 'v1.2.2'
stop=threading.Event()
signal.signal(signal.SIGTERM, lambda *args: stop.set())
def handler(op,args):
    return {'python':sys.executable,'readonly':bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),
            'source_manifest_sha256':identity}
with Relay(a.socket,handler) as relay:
    stop.wait(30)
Path(a.receipt).write_text(json.dumps(relay.receipt))
'''.replace('ROOT', repr(str(adapter.ASSETS.parents[2]))).replace('ASSETS', repr(str(adapter.ASSETS))))
    setup['tool_relay']['program'] = str(program)
    setup['tool_relay']['read_paths'].append(str(program))
    handle = tool_relay.start(setup, ready_timeout_s=20)
    try:
        result = call(handle.directory / 'tool.sock', 'tools')
        assert result['python'] == native['python']
        assert result['readonly']
        assert result['source_manifest_sha256']
        assert 'PRIVATE-GOLD-SENTINEL' not in json.dumps(result)
    finally:
        handle.cleanup()


def test_prepared_change_does_not_disclose_private_bytes(tmp_path):
    (tmp_path / '_src').mkdir()
    (tmp_path / '_src/cases.json').write_text('PRIVATE-GOLD-SENTINEL')
    with pytest.raises(ValueError, match='catalog digest mismatch') as error:
        bootstrap_module().validate_prepared(tmp_path)
    assert 'PRIVATE-GOLD-SENTINEL' not in str(error.value)


def test_native_changed_source_is_unmeasured_before_evaluation(native, tmp_path, monkeypatch):
    from eval.verification.verifier import _verify
    source = tmp_path / 'changed-source'
    shutil.copytree(adapter._source_root(), source)
    (source / 'agentdojo/__init__.py').write_text('raise RuntimeError("PRIVATE-GOLD-SENTINEL")')
    monkeypatch.setattr(adapter, '_source_root', lambda: source)
    spec = adapter.VERIFY['agentdojo/0']
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    result = _verify(work, spec, '', sandbox=True, timeout_s=30, env={'kind': 'files'})
    assert result['score'] is None and result['outcome'] == 'unmeasured', json.dumps(result)
    evidence = work / 'verifier/evidence.json'
    assert evidence.is_file(), json.dumps(result)
    assert json.loads(evidence.read_text())['error'] == 'ValueError'
    assert 'PRIVATE-GOLD-SENTINEL' not in json.dumps(result)


def test_runtime_selection_does_not_cache_changed_packages(monkeypatch):
    calls = []
    def resolve(name):
        calls.append(name)
        if len(calls) == 1:
            return {'runtime_id': 'a' * 64}
        raise dataset_runtime.DatasetRuntimeUnavailable('runtime package inventory changed')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', resolve)
    assert adapter._execution_runtime()['runtime_id'] == 'a' * 64
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='inventory changed'):
        adapter._execution_runtime()
    assert calls == ['agentdojo', 'agentdojo']


def test_same_path_source_and_case_changes_change_verification_context(tmp_path, monkeypatch):
    from harnessgrad import verification_context
    venv = tmp_path / 'artifact/venv'
    runtime = {'dataset': 'agentdojo', 'profile': 'execution', 'runtime_id': 'a' * 64,
               'manifest_path': str(venv.parent / 'artifact.json'), 'path': str(venv),
               'python': str(venv / 'bin/python'), 'runtime_paths': [str(venv)]}
    monkeypatch.setattr(adapter, '_execution_runtime', lambda: runtime)
    monkeypatch.setattr(adapter, 'DATASET_ROOT', tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    source_file = source / 'source.py'
    source_file.write_text('synthetic original source')
    monkeypatch.setattr(adapter, '_source_root', lambda: source)
    case = tmp_path / adapter.PREPARED / '0/case.json'
    case.parent.mkdir(parents=True)
    case.write_text('PRIVATE-GOLD-SENTINEL')
    tasks = [{'task_id': 'agentdojo/0', 'goal': 'synthetic'}]
    def capture():
        return verification_context.capture(tasks, {'agentdojo/0': 'official utility'},
                                            adapter.SETUP, adapter.VERIFY, adapter.ENV)
    original = capture()
    source_file.write_text('synthetic changed source')
    changed_source = capture()
    assert original != changed_source
    case.write_text('PRIVATE-CHANGED-SENTINEL')
    assert capture() != changed_source
    assert 'PRIVATE-' not in json.dumps(original)
