"""CostBench execution roles select one artifact independently of platform Python.

Real oracle/noop controls are opt-in via HG_TEST_COSTBENCH_EXECUTION_RUNTIME_ROOT.
The tests never install dependencies or contact a model.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.datasets import costbench, costbench_dynamic
from harnessgrad import dataset_runtime


@pytest.fixture
def execution(monkeypatch, tmp_path):
    helper = costbench._execution()
    helper.runtime.cache_clear()
    selected = tmp_path / 'selected/venv'
    runtime = {'dataset': 'costbench', 'profile': 'execution', 'path': str(selected),
               'python': str(selected / 'bin/python'), 'runtime_paths': [str(selected), '/usr'],
               'manifest_path': str(selected.parent / 'artifact.json'), 'runtime_id': 'a' * 64}
    resolves = []

    def resolve(name):
        resolves.append(name)
        return runtime

    monkeypatch.setattr(dataset_runtime, 'resolve_execution', resolve)
    monkeypatch.setattr(dataset_runtime, 'check_execution', lambda name: {
        'present': True, 'status': 'ready', 'scope': 'dataset-execution-runtime',
        'detail': 'verified runtime', 'runtime': runtime})
    root = tmp_path / 'data'
    prepared = root / 'prepared'
    folder = prepared / 'chosen'
    folder.mkdir(parents=True)
    public = b'{"query":"authored public task"}'
    (folder / 'public.json').write_bytes(public)
    (folder / 'private.json').write_text('{"private":"authored gold"}')
    row = {'path': 'chosen', 'public_sha256': hashlib.sha256(public).hexdigest(),
           'private_sha256': hashlib.sha256((folder / 'private.json').read_bytes()).hexdigest()}
    for adapter in (costbench, costbench_dynamic):
        monkeypatch.setattr(adapter, 'DATASET_ROOT', root)
        monkeypatch.setattr(adapter, 'PREPARED', prepared)
        monkeypatch.setattr(adapter, 'roster', lambda: {'chosen': row, 'unselected': {'path': 'absent'}})
    yield SimpleNamespace(helper=helper, runtime=runtime, resolves=resolves, root=root,
                          prepared=prepared, folder=folder, row=row)
    helper.runtime.cache_clear()


@pytest.mark.parametrize('adapter', [costbench, costbench_dynamic])
def test_metadata_import_does_not_select_or_prepare_execution_runtime(monkeypatch, adapter):
    helper = costbench._execution()
    helper.runtime.cache_clear()
    monkeypatch.setattr(dataset_runtime, 'resolve_execution',
                        lambda *args, **kwargs: pytest.fail('metadata selected runtime'))
    path = Path(adapter.__file__)
    spec = importlib.util.spec_from_file_location('_metadata_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.KIND == 'dataset' and module.SPLIT_SOURCE == 'declared'
    assert module.downloads()


@pytest.mark.parametrize('adapter', [costbench, costbench_dynamic])
def test_selected_service_and_verifier_share_the_exact_artifact(execution, adapter):
    assert len(adapter.SETUP) == len(adapter.VERIFY) == 2
    assert list(adapter.SETUP) == list(adapter.VERIFY) == ['chosen', 'unselected']
    assert execution.resolves == []
    setup, verifier = adapter.SETUP['chosen'], adapter.VERIFY['chosen']
    service = setup['tool_relay']
    assert execution.resolves == ['costbench']
    assert service['python'] == verifier['argv'][0] == execution.runtime['python']
    assert verifier['argv'][1:4] == ['-I', '-B', 'costbench_code/verify/check.py']
    assert service['python_runtime']['role'] == 'service'
    assert verifier['python_runtime']['role'] == 'verifier'
    assert service['python_runtime']['runtime_id'] == verifier['python_runtime']['runtime_id']
    assert verifier['runtime_paths'] == execution.runtime['runtime_paths']
    assert 'costbench_runtime' not in {item['path'] for item in verifier['inputs']}
    assert all('authored gold' not in json.dumps(item) for item in setup['files'])
    paths = service['read_paths']
    assert str(execution.root) not in paths
    assert str(execution.folder) in paths
    assert str(execution.root / 'bundle') in paths
    assert str(execution.root / 'search-spaces') in paths
    assert str(adapter.ASSETS.resolve()) in paths
    assert str(adapter.TRANSPORT.resolve()) in paths
    assert str(Path(adapter.__file__).resolve().parents[2]) not in paths


@pytest.mark.parametrize('adapter', [costbench, costbench_dynamic])
def test_unknown_selection_fails_without_selecting_runtime(execution, adapter):
    with pytest.raises(KeyError):
        adapter.SETUP['missing']
    with pytest.raises(KeyError):
        adapter.VERIFY['missing']
    assert execution.resolves == []


def test_runtime_property_uses_managed_artifact_and_ignores_legacy_override(execution, monkeypatch):
    monkeypatch.setenv('HG_DATA_COSTBENCH_RUNTIME', '/legacy/runtime/must-not-be-selected')
    assert costbench.RUNTIME == costbench_dynamic.RUNTIME == Path(execution.runtime['path'])
    assert execution.resolves == ['costbench']


@pytest.mark.parametrize('adapter', [costbench, costbench_dynamic])
def test_provision_status_preserves_invalid_runtime(execution, adapter, monkeypatch):
    failure = {'present': False, 'status': 'invalid', 'scope': 'dataset-execution-runtime',
               'detail': 'runtime file inventory changed'}
    monkeypatch.setattr(dataset_runtime, 'check_execution', lambda name: failure)
    result = adapter.provision_status()
    assert result['present'] is False and result['status'] == 'invalid'
    assert result['execution_runtime'] == failure
    assert execution.resolves == []


@pytest.mark.parametrize('adapter', [costbench, costbench_dynamic])
def test_provision_status_checks_data_and_runtime_without_launch(execution, adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'roster', lambda: {'chosen': execution.row})
    result = adapter.provision_status()
    assert result['present'] is True and result['status'] == 'ready'
    assert result['tasks'] == 1 and execution.resolves == []
    (execution.folder / 'private.json').unlink()
    missing = adapter.provision_status()
    assert missing['present'] is False and missing['status'] == 'data_missing'


@pytest.fixture
def real_runtime(monkeypatch):
    store = os.environ.get('HG_TEST_COSTBENCH_EXECUTION_RUNTIME_ROOT')
    if not store:
        pytest.skip('set HG_TEST_COSTBENCH_EXECUTION_RUNTIME_ROOT to a prepared store')
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', str(Path(store).resolve(strict=True)))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    helper = costbench._execution()
    helper.runtime.cache_clear()
    checked = dataset_runtime.check_execution('costbench')
    assert checked['present'], checked
    yield checked['runtime']
    helper.runtime.cache_clear()


@pytest.mark.parametrize('adapter', [costbench, costbench_dynamic], ids=['static', 'dynamic'])
@pytest.mark.parametrize('split', ['train', 'test'])
@pytest.mark.parametrize('oracle', [False, True], ids=['noop', 'oracle'])
def test_real_controls_record_selected_service_and_scorer_python(real_runtime, adapter, split,
                                                                oracle, tmp_path):
    assert adapter.provision_status()['present'], 'prepared CostBench payload is required'
    task_id = next(tid for tid, row in adapter.roster().items()
                   if row['split'] == split and row.get('mode', 'cost_change') == 'cost_change')
    control_path = (adapter.ASSETS / 'control.py' if adapter is costbench else
                    adapter.ASSETS.parent / 'costbench_dynamic/control.py')
    spec = importlib.util.spec_from_file_location('_execution_runtime_control', control_path)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    value, result = control.run(oracle, True, task_id, tmp_path)
    assert value == float(oracle), result
    service = result['runtime_services']['tool_relay']
    assert service['cleanup']['complete']
    observations = [service['receipt']['python_runtime'],
                    result['verdict']['evidence']['python_runtime']]
    for observed in observations:
        assert observed['python'] == real_runtime['python']
        assert observed['prefix'] == real_runtime['path']
        assert observed['version'][:2] == [3, 12]
        assert observed['isolated'] and observed['readonly']
        assert observed['site_paths'] == [str(Path(real_runtime['path']) / 'lib/python3.12/site-packages')]
        assert all(Path(origin).is_relative_to(real_runtime['path'])
                   for origin in observed['package_origins'].values())


@pytest.mark.parametrize('adapter', [costbench, costbench_dynamic], ids=['static', 'dynamic'])
def test_real_services_share_dependencies_without_sharing_episode_state(real_runtime, adapter):
    from eval.run import tool_relay
    from eval.service.tool_transport import call

    tid = next(tid for tid, row in adapter.roster().items()
               if row['split'] == 'test' and row.get('mode', 'cost_change') == 'cost_change')
    row = adapter.roster()[tid]
    case = json.loads((adapter.PREPARED / row['path'] / 'private.json').read_text())
    handles = []
    # Keep the creating task threads alive until both guarded children stop.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(tool_relay.start, adapter.SETUP[tid], context={
            'task_id': tid, 'run_id': 'costbench-state-control',
            'trial_id': str(index), 'seed': '42'}) for index in range(2)]
        try:
            handles = [future.result() for future in futures]
            first, second = handles
            assert first.directory != second.directory and first.path != second.path
            assert call(first.path, 'status', {})['steps'] == call(second.path, 'status', {})['steps'] == 0
            call(first.path, 'turn', {'content': None, 'calls': [case['oracle_actions'][0]]})
            assert call(first.path, 'status', {})['steps'] == 1
            assert call(second.path, 'status', {})['steps'] == 0
            for index, handle in enumerate(handles):
                handle.stop()
                assert handle.fault() is None
                sealed = json.loads((Path(handle.recording_input()['from']) / 'episode.json').read_text())
                assert sealed['binding']['trial_id'] == str(index)
                assert handle.receipt['python_runtime']['prefix'] == real_runtime['path']
                assert handle.receipt['python_runtime']['readonly']
        finally:
            for future in futures:
                try:
                    handle = future.result()
                except Exception:
                    continue
                handle.cleanup()
                assert handle.report()['cleanup']['complete']
