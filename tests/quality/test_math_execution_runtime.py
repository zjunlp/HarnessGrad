"""MATH runtime and source custody checks on authored, model-free examples."""
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
import venv

import pytest

from data.datasets import math as math_dataset, math500
from data.scaffolds.native_qa import CATALOG, Dataset, math_execution, scoring
from eval.verification import verifier
from harnessgrad import dataset_runtime, dataset_runtime_identity
from tools import datasets
from test_native_qa_adapters import fixture, prepared_math, source_row
from test_python_execution_launch import changed_artifact

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clear_selection():
    math_execution.runtime.cache_clear()
    yield
    math_execution.runtime.cache_clear()


@pytest.fixture(scope='module')
def clean_platform_python():
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='hg-math-platform-', dir=cache))
    try:
        venv.EnvBuilder(with_pip=False, system_site_packages=False).create(root / 'venv')
        yield root / 'venv/bin/python'
    finally:
        shutil.rmtree(root)


@pytest.mark.parametrize('adapter', [math_dataset, math500])
def test_metadata_import_has_no_data_or_runtime_side_effects(adapter, monkeypatch):
    monkeypatch.setattr(math_execution, 'runtime', Mock(side_effect=AssertionError('selected runtime')))
    monkeypatch.setattr(Dataset, 'records', Mock(side_effect=AssertionError('read native data')))
    spec = importlib.util.spec_from_file_location('_metadata_' + adapter.__name__, adapter.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.MANAGED_EXECUTION_RUNTIME == 'math'
    assert module.downloads()


@pytest.mark.parametrize('name', ['math', 'math500'])
def test_local_preparation_and_loading_work_without_third_party_packages(
        clean_platform_python, tmp_path, name):
    local = []
    for split in CATALOG[name]['splits']:
        raw = tmp_path / (split + '.jsonl')
        raw.write_text(json.dumps(source_row(name, split)) + '\n')
        local.append(split + '=' + str(raw))
    code = '''
import importlib.util, json, sys
from pathlib import Path
for name in ('pip', 'math_verify', 'sympy', 'datasets', 'huggingface_hub', 'pyarrow'):
    assert importlib.util.find_spec(name) is None, name
sys.path.insert(0, sys.argv[1])
from data.scaffolds.native_qa import Dataset
from data.scaffolds.native_qa.prepare import prepare
name, root = sys.argv[2], Path(sys.argv[3])
manifest = prepare(name, root, json.loads(sys.argv[4]))
assert manifest['acquisition'].startswith('local-native-import')
adapter = Dataset(name, root)
tasks, _ = adapter.load()
assert len(tasks) == len(manifest['files'])
assert all('solution' not in task for task in tasks)
status = adapter.provision_status()
assert status['present'] is False and status['status'] == 'unavailable'
assert '--dataset math --runtime execution' in status['detail']
assert 'math_verify' not in sys.modules
print(json.dumps({'tasks': len(tasks), 'scorer': status['status']}))
'''
    env = {'PATH': os.defpath, 'HG_DATASET_RUNTIME_ROOT': str(tmp_path / 'absent-store')}
    result = subprocess.run([str(clean_platform_python), '-I', '-B', '-c', code, str(ROOT),
                             name, str(tmp_path / 'data'), json.dumps(local)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['scorer'] == 'unavailable'
    assert not (tmp_path / 'absent-store').exists()


def test_both_datasets_bind_one_artifact_into_measurement_identity(prepared_math, tmp_path):
    tasks, specs = [], {}
    for name in ('math', 'math500'):
        adapter = fixture(name, tmp_path / name)
        selected = adapter.load()[0]
        tasks.extend(selected)
        specs.update({row['task_id']: adapter.verifier(row) for row in adapter.records()})
    identity = dataset_runtime_identity.for_evaluation(None, tasks, {}, specs)
    assert len(identity['artifacts']) == 1
    artifact, = identity['artifacts'].values()
    assert artifact['runtime_id'] == prepared_math['runtime_id'] and artifact['dataset'] == 'math'
    assert set(identity['tasks']) == {task['task_id'] for task in tasks}
    for spec in specs.values():
        assert spec['argv'][:3] == [prepared_math['python'], '-I', '-B']
        assert spec['runtime_paths'] == prepared_math['runtime_paths']
        assert 'math_execution.py' not in {Path(item['path']).name for item in spec['inputs']}
        assert spec['python_runtime']['role'] == 'verifier'


@pytest.mark.parametrize('name', ['math', 'math500'])
def test_h0_preflight_uses_the_shared_artifact_on_a_host_without_math_verify(
        prepared_math, tmp_path, name):
    adapter = fixture(name, tmp_path / 'data')
    ids = [task['task_id'] for task in adapter.load()[0] if '/test/' in task['task_id']]
    env = {**os.environ, 'HG_DATA_' + name.upper(): str(adapter.root)}
    result = dataset_runtime_identity.resolve_h0(name, side='eval', task_ids=ids, env=env)
    assert set(result['tasks']) == set(ids)
    assert result['provider'] is None and len(result['artifacts']) == 1
    artifact, = result['artifacts'].values()
    assert artifact['dataset'] == 'math' and artifact['runtime_id'] == prepared_math['runtime_id']


@pytest.mark.parametrize('name', ['math', 'math500'])
@pytest.mark.parametrize('status', ['unavailable', 'invalid', 'ready'])
def test_readiness_combines_data_with_a_fresh_artifact_check(tmp_path, monkeypatch, name, status):
    adapter = fixture(name, tmp_path / 'data')
    state = {'present': status == 'ready', 'status': status,
             'scope': 'dataset-execution-runtime', 'detail': 'authored artifact state'}
    check = Mock(return_value=state)
    monkeypatch.setattr(dataset_runtime, 'check_execution', check)
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', Mock(side_effect=AssertionError('selected')))
    observed = adapter.provision_status()
    assert observed['present'] is state['present'] and observed['status'] == status
    assert observed['execution_runtime'] == state
    assert observed['tasks'] == len(CATALOG[name]['splits'])
    check.assert_called_once_with('math')


@pytest.mark.parametrize('name', ['math', 'math500'])
@pytest.mark.parametrize('oracle', [True, False])
def test_sandboxed_grade_imports_only_bound_packages_and_trusted_sources(
        prepared_math, tmp_path, monkeypatch, name, oracle):
    adapter = fixture(name, tmp_path / 'data')
    row = adapter.records()[-1]
    work = tmp_path / 'task'; work.mkdir()
    (work / 'answer.txt').write_text(r'\boxed{0.5}' if oracle else r'\boxed{0.4}')
    poison = work / 'verifier/native_qa'; poison.mkdir(parents=True)
    for module in ('math_worker.py', 'math_grade.py', 'scoring.py'):
        source = poison / module
        source.write_text('raise AssertionError("actor-controlled scorer")')
        py_compile.compile(str(source), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    (poison / '__init__.py').write_text('raise AssertionError("actor package")')
    (poison / 'math_verify.py').write_text('raise AssertionError("actor Math-Verify")')
    (work / 'sympy.py').write_text('raise AssertionError("actor SymPy")')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', str(poison))
    monkeypatch.setenv('PYTHONHOME', str(work))
    result = verifier._verify(work, adapter.verifier(row), '', sandbox=True, timeout_s=30,
                              env={'kind': 'files'}, task_id=row['task_id'])
    assert result.get('score') == float(oracle), json.dumps(result, indent=2)
    observed = result['evidence']['python_runtime']
    assert observed['python'] == prepared_math['python'] and observed['prefix'] == prepared_math['path']
    assert observed['isolated'] and observed['no_bytecode'] and observed['readonly']
    assert observed['package_versions']['math_verify'] == '0.9.0'
    assert observed['site_paths'] == [str(Path(prepared_math['path']) / 'lib/python3.12/site-packages')]
    assert all(Path(path).is_relative_to(prepared_math['path'])
               for path in observed['package_origins'].values())
    assert result['python_runtime']['runtime_id'] == prepared_math['runtime_id']
    assert result['evidence']['scorer_input']['verified'] is True
    assert 'frac' not in json.dumps(result['evidence'])


@pytest.mark.parametrize('adapter', [math_dataset, math500])
def test_public_score_does_not_query_platform_math_verify(prepared_math, monkeypatch, adapter):
    # Only the inner runtime may enforce package versions. Poisoning the host's
    # metadata access must not prevent a valid managed score.
    metadata = SimpleNamespace(version=Mock(side_effect=AssertionError('platform package check')))
    monkeypatch.setattr(scoring, 'importlib', SimpleNamespace(metadata=metadata))
    result = adapter.score(r'\boxed{1+1}', {'answer': r'\boxed{2}'})
    assert result['reward'] == 1
    assert result['python_runtime']['python'] == prepared_math['python']
    metadata.version.assert_not_called()


@pytest.mark.parametrize('gold,prediction', [
    (r'\boxed{\frac{1}{2}}', r'\boxed{0.5}'),
    (r'\boxed{2}', r'\boxed{1+1}'),
    (r'first \boxed{1}, then \boxed{2}', r'\boxed{2}'),
    (r'\boxed{2}', r'\boxed{3}'),
    (r'\boxed{2}', r'\boxed{2} then \boxed{3}'),
    (r'\boxed{2}', r'\boxed{2} then \boxed{'),
    (r'\boxed{2}', ''),
])
def test_managed_public_score_matches_the_pinned_entrypoint(prepared_math, gold, prediction):
    original = subprocess.run([prepared_math['python'], '-I', '-B',
                               str(ROOT / 'data/scaffolds/native_qa/math_grade.py')],
                              input=json.dumps({'gold': gold, 'prediction': prediction}),
                              capture_output=True, text=True, timeout=15)
    assert original.returncode == 0, original.stderr
    current = scoring.score('math', prediction, {'answer': gold})
    assert current['reward'] == float(json.loads(original.stdout)['correct'])


def test_unparseable_reference_is_unmeasured_and_does_not_expose_gold(
        prepared_math, tmp_path, monkeypatch):
    adapter = Dataset('math500', tmp_path / 'unused-data')
    row = {'task_id': 'math500/test/authored-invalid-reference',
           'gold': {'answer': r'PRIVATE_REFERENCE_SENTINEL \boxed{}'}}
    work = tmp_path / 'task'; work.mkdir()
    (work / 'answer.txt').write_text('PRIVATE_REFERENCE_SENTINEL')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    result = verifier._verify(work, adapter.verifier(row), '', sandbox=True, timeout_s=30,
                              env={'kind': 'files'}, task_id=row['task_id'])
    assert result.get('score') is None and result['outcome'] == 'unmeasured', result
    # require_exit_zero rejects before attaching evidence to the verdict; the
    # check's failure record remains in its declared output file.
    evidence = json.loads((work / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is False
    assert 'PRIVATE_REFERENCE_SENTINEL' not in json.dumps([result, evidence])


def test_direct_score_rechecks_cached_artifact_before_launch(prepared_math, monkeypatch):
    math_execution.runtime()
    launch = Mock(side_effect=AssertionError('changed artifact reached a worker'))
    monkeypatch.setattr(math_execution, 'subprocess', SimpleNamespace(Popen=launch))
    with changed_artifact(prepared_math):
        with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable):
            math_execution.score('math500', '2', {'answer': r'\boxed{2}'})
    launch.assert_not_called()


def test_math_inner_timeout_is_preserved_as_an_error(monkeypatch):
    launch = Mock(side_effect=subprocess.TimeoutExpired('authored grader', 15))
    monkeypatch.setattr(scoring, 'subprocess', SimpleNamespace(run=launch))
    monkeypatch.setattr(scoring, 'importlib', SimpleNamespace(
        metadata=SimpleNamespace(version=lambda _: '0.9.0')))
    with pytest.raises(subprocess.TimeoutExpired):
        scoring.score('math', '2', {'answer': r'\boxed{2}'}, _math_runtime=True)
    assert launch.call_args.kwargs['timeout'] == 15
    assert launch.call_args.args[0][:4] == [sys.executable, '-I', '-B', '-X']


def test_direct_timeout_stops_the_whole_worker_group(prepared_math, monkeypatch):
    proc = Mock(pid=12345)
    proc.communicate.side_effect = [subprocess.TimeoutExpired('authored worker', 30), ('', '')]
    launch = Mock(return_value=proc)
    monkeypatch.setattr(math_execution, 'subprocess', SimpleNamespace(Popen=launch, PIPE=subprocess.PIPE))
    kill = Mock()
    monkeypatch.setattr(math_execution, 'os', SimpleNamespace(defpath=os.defpath, killpg=kill))
    with pytest.raises(subprocess.TimeoutExpired):
        math_execution.score('math500', '2', {'answer': r'\boxed{2}'})
    kill.assert_called_once_with(12345, signal.SIGKILL)
    assert proc.communicate.call_count == 2
    assert launch.call_args.kwargs['start_new_session'] is True
    assert not Path(launch.call_args.kwargs['cwd']).exists()


@pytest.mark.parametrize('name', ['math', 'math500'])
@pytest.mark.parametrize('oracle', [True, False])
def test_control_cli_runs_from_a_clean_platform(
        prepared_math, clean_platform_python, tmp_path, name, oracle):
    adapter = fixture(name, tmp_path / 'data')
    env = {'PATH': os.defpath, 'HOME': str(tmp_path), 'LANG': 'C.UTF-8',
           'HG_DATASET_RUNTIME_ROOT': os.environ['HG_DATASET_RUNTIME_ROOT'],
           'HG_DATA_' + name.upper(): str(adapter.root)}
    result = subprocess.run([str(clean_platform_python), '-I', '-B',
                             str(ROOT / 'data/scaffolds/native_qa/control.py'),
                             '--dataset', name, '--oracle' if oracle else '--noop'],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert float(result.stdout) == float(oracle)


@pytest.mark.parametrize('name', ['math', 'math500'])
def test_download_check_reads_only_the_shared_execution_packages(prepared_math, tmp_path, monkeypatch, name):
    root = tmp_path / 'data'; root.mkdir()
    (root / 'source').write_text('authored downloaded bytes')
    module = SimpleNamespace(DATASET_ROOT=root, MANAGED_EXECUTION_RUNTIME='math')
    monkeypatch.setattr(datasets, '_download_adapter', lambda _: module)
    host = Mock(side_effect=AssertionError('checked platform package'))
    monkeypatch.setattr(datasets, 'importlib', SimpleNamespace(metadata=SimpleNamespace(version=host)))
    entries = [{'kind': 'http', 'name': 'authored source', 'dst': 'source', 'version': 'authored'},
               {'kind': 'pip', 'name': 'Math-Verify', 'package': 'math-verify', 'version': '0.9.0'}]
    result = datasets.check_dataset(name, entries)
    assert result['present'] and result['data_present']
    assert result['runtime']['runtime']['runtime_id'] == prepared_math['runtime_id']
    assert result['status']['scope'] == 'declared-downloads-and-execution-runtime'
    assert 'dataset runtime' in result['entries'][1]['detail']
    host.assert_not_called()


@pytest.mark.parametrize('status', ['unavailable', 'invalid'])
def test_download_check_never_uses_host_packages_for_a_missing_or_invalid_artifact(tmp_path, monkeypatch, status):
    module = SimpleNamespace(DATASET_ROOT=tmp_path, MANAGED_EXECUTION_RUNTIME='math')
    monkeypatch.setattr(datasets, '_download_adapter', lambda _: module)
    state = {'present': False, 'status': status, 'scope': 'dataset-execution-runtime',
             'detail': 'authored unavailable artifact'}
    monkeypatch.setattr(dataset_runtime, 'check_execution', lambda _: state)
    host = Mock(side_effect=AssertionError('checked platform package'))
    monkeypatch.setattr(datasets, 'importlib', SimpleNamespace(metadata=SimpleNamespace(version=host)))
    result = datasets.check_dataset('math500', [
        {'kind': 'pip', 'name': 'Math-Verify', 'package': 'math-verify', 'version': '0.9.0'}])
    assert not result['present'] and result['runtime']['status'] == status
    host.assert_not_called()
