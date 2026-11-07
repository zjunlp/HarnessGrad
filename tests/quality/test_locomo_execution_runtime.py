"""Synthetic LoCoMo controls; runtime installation is always an explicit prior step."""
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
import venv

import pytest

from data.datasets import locomo
from data.scaffolds import command_control
from eval.verification import verifier
from harnessgrad import dataset_runtime, dataset_runtime_identity
from test_locomo_dataset import prepared_runtime, synthetic
from test_python_execution_launch import changed_artifact

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clear_selection():
    locomo._execution_runtime.cache_clear()
    yield
    locomo._execution_runtime.cache_clear()


@pytest.fixture(scope='module')
def clean_platform_python():
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='hg-locomo-clean-platform-', dir=cache))
    try:
        venv.EnvBuilder(with_pip=False, system_site_packages=False).create(root / 'venv')
        yield root / 'venv/bin/python'
    finally:
        shutil.rmtree(root)


def test_metadata_and_public_tasks_never_select_execution_runtime(synthetic, monkeypatch):
    resolve = Mock(side_effect=AssertionError('public metadata selected a runtime'))
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', resolve)
    spec = importlib.util.spec_from_file_location('_locomo_metadata', locomo.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.downloads()
    tasks, _ = locomo.load()
    assert tasks == [synthetic]
    assert locomo.SETUP[synthetic['task_id']]['files']
    assert list(locomo.VERIFY) == [synthetic['task_id']]
    assert len(locomo.VERIFY) == 1
    with pytest.raises(KeyError):
        locomo.VERIFY['absent']
    resolve.assert_not_called()


def test_verifier_requires_explicit_preparation(synthetic, tmp_path, monkeypatch):
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', str(tmp_path / 'empty-store'))
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='--runtime execution'):
        locomo.VERIFY[synthetic['task_id']]
    assert not (tmp_path / 'empty-store').exists()


def test_declaration_has_complete_pins_without_a_loading_runtime():
    assert dataset_runtime.descriptor('locomo') is None
    spec = dataset_runtime.execution_descriptor('locomo')
    assert spec['python'] == '3.12'
    assert spec['pins']['nltk'] == '3.10.4'
    assert set(spec['pins']) == {'nltk', 'click', 'cloudpickle', 'defusedxml', 'joblib', 'regex', 'tqdm'}


@pytest.mark.parametrize('status', ['unavailable', 'invalid', 'ready'])
def test_readiness_checks_execution_without_resolving_or_scoring(synthetic, tmp_path, monkeypatch, status):
    monkeypatch.setattr(locomo, 'DATASET_ROOT', tmp_path)
    state = {'present': status == 'ready', 'status': status,
             'scope': 'dataset-execution-runtime', 'detail': 'synthetic runtime state'}
    check = Mock(return_value=state)
    monkeypatch.setattr(dataset_runtime, 'check_execution', check)
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', Mock(side_effect=AssertionError('resolved')))
    result = locomo.provision_status()
    assert result['present'] is state['present'] and result['status'] == status
    assert result['execution_runtime'] == state and result['tasks'] == 1 and result['questions'] == 2
    check.assert_called_once_with('locomo')


def test_aggregation_report_works_without_nltk_or_an_execution_artifact(
        synthetic, clean_platform_python, tmp_path):
    # Do not use pytest's interpreter, which may contain the reference's NLTK.
    code = '''
import importlib.util, json, sys
from pathlib import Path
assert importlib.util.find_spec('nltk') is None
sys.path.insert(0, sys.argv[1])
from data.datasets import locomo
row = json.loads(sys.stdin.read())
tid = 'locomo/' + row['sample_id']
locomo.rows = lambda: {tid: row}
report = locomo._load_asset('report.py')
missing = report.summarize([], [tid])
assert missing['unmeasured_tasks'] == 1 and 'micro_avg_reward' not in missing
gold = locomo.gold_record(tid)
record = {'schema': 'hg-locomo-evidence-v1', 'task_id': tid, 'measured': True,
          'metric': locomo.METRIC, 'source_sha256': locomo.DATA_SHA256,
          'conversation_sha256': gold['conversation_sha256'], 'num_questions': 2,
          'score_sum': 1, 'per_question': [{'index': 0, 'category': 4, 'reward': 1},
                                        {'index': 1, 'category': 5, 'reward': 0}]}
complete = report.summarize([record], [tid])
assert complete['micro_avg_reward'] == .5
assert 'nltk' not in sys.modules
print(json.dumps(complete))
'''
    result = subprocess.run([str(clean_platform_python), '-I', '-B', '-c', code, str(ROOT)],
                            input=json.dumps(locomo.rows()[synthetic['task_id']]), cwd=tmp_path,
                            env={'PATH': os.defpath, 'HG_DATASET_RUNTIME_ROOT': str(tmp_path / 'empty')},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['micro_avg_reward'] == .5
    assert not (tmp_path / 'empty').exists()


def test_selected_runtime_is_bound_to_measurement_identity(synthetic, prepared_runtime):
    tid = synthetic['task_id']
    spec = locomo.VERIFY[tid]
    assert spec['argv'][:3] == [prepared_runtime['python'], '-I', '-B']
    binding = spec['python_runtime']
    assert binding['role'] == 'verifier' and binding['dataset'] == 'locomo'
    assert binding['runtime_id'] == prepared_runtime['runtime_id']
    assert spec['runtime_paths'] == prepared_runtime['runtime_paths']
    identity = dataset_runtime_identity.for_evaluation(None, [synthetic], {}, {tid: spec})
    assert set(identity['tasks'][tid]) == {'verifier'}
    assert next(iter(identity['artifacts'].values()))['runtime_id'] == prepared_runtime['runtime_id']


@pytest.mark.parametrize('oracle', [True, False], ids=['oracle', 'noop'])
def test_sandboxed_verifier_uses_readonly_nltk_and_rematerializes_private_inputs(
        synthetic, prepared_runtime, tmp_path, monkeypatch, oracle):
    tid = synthetic['task_id']
    command_control.materialize(locomo, synthetic, tmp_path)
    assert not (tmp_path / 'locomo_verify/gold.json').exists()
    if oracle:
        command_control.solve(locomo, synthetic, tmp_path)
    poison = tmp_path / 'locomo_verify'
    poison.mkdir()
    (poison / 'check.py').write_text('raise AssertionError("actor-modified verifier")')
    (poison / 'scoring.py').write_text('raise AssertionError("actor-modified scorer")')
    py_compile.compile(str(poison / 'scoring.py'), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    (poison / 'nltk').mkdir()
    (poison / 'nltk/__init__.py').write_text('raise AssertionError("actor scorer-directory NLTK")')
    (tmp_path / 'nltk.py').write_text('raise AssertionError("actor NLTK")')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setenv('PYTHONHOME', str(tmp_path))
    result = verifier._verify(tmp_path, locomo.VERIFY[tid], '', sandbox=True, timeout_s=30,
                              env={'kind': 'files'}, task_id=tid)
    assert result.get('score') == float(oracle), json.dumps(result, indent=2)
    assert result['python_runtime']['runtime_id'] == prepared_runtime['runtime_id']
    evidence = result['evidence']
    observed = evidence['python_runtime']
    assert observed['python'] == prepared_runtime['python'] and observed['prefix'] == prepared_runtime['path']
    assert observed['version'][:2] == [3, 12]
    assert observed['isolated'] and observed['readonly'] and observed['no_bytecode']
    assert observed['package_versions'] == {'nltk': '3.10.4'}
    assert observed['site_paths'] == [str(Path(prepared_runtime['path']) / 'lib/python3.12/site-packages')]
    assert all(Path(origin).is_relative_to(prepared_runtime['path'])
               for origin in observed['package_origins'].values())
    assert 'SECRET_EVIDENCE' not in json.dumps(evidence)


@pytest.mark.parametrize('change', ['artifact', 'role', 'python', 'paths', 'missing-I', 'missing-B'])
def test_local_control_rechecks_the_binding_and_clears_stale_reward(
        synthetic, prepared_runtime, tmp_path, monkeypatch, change):
    tid = synthetic['task_id']
    spec = locomo.VERIFY[tid]
    if change == 'role':
        spec['python_runtime']['role'] = 'service'
    elif change == 'python':
        spec['argv'][0] = sys.executable
    elif change == 'paths':
        spec['runtime_paths'].append('/usr')
    elif change.startswith('missing-'):
        spec['argv'].remove('-' + change[-1])
    module = SimpleNamespace(VERIFY={tid: spec})
    (tmp_path / 'verifier').mkdir()
    (tmp_path / 'verifier/reward.txt').write_text('1')
    (tmp_path / 'verifier/evidence.json').write_text('{"measured": true}')
    launch = Mock(side_effect=AssertionError('invalid control reached subprocess'))
    # Leave dataset_runtime's independent probes intact while trapping the
    # control's own launch, rather than replacing the global subprocess module.
    monkeypatch.setattr(command_control, 'subprocess', SimpleNamespace(run=launch))
    with changed_artifact(prepared_runtime) if change == 'artifact' else nullcontext():
        reward, detail = command_control.run_check(module, synthetic, tmp_path)
    assert reward == -1 and json.loads(detail)['measured'] is False
    assert not (tmp_path / 'verifier/reward.txt').exists()
    assert not (tmp_path / 'verifier/evidence.json').exists()
    launch.assert_not_called()


@pytest.mark.parametrize('oracle', [True, False], ids=['oracle', 'noop'])
def test_full_platform_control_from_python_without_nltk(
        synthetic, prepared_runtime, clean_platform_python, tmp_path, oracle):
    code = '''
import importlib.util, json, sys
from pathlib import Path
assert importlib.util.find_spec('nltk') is None
sys.path.insert(0, sys.argv[1])
from data.datasets import locomo
from data.scaffolds import command_control
row = json.loads(sys.stdin.read())
tid = 'locomo/' + row['sample_id']
locomo.rows = lambda: {tid: row}
task = locomo.control_task(tid)
result = command_control.run_platform(locomo, task, oracle=sys.argv[3] == '1', workdir=Path(sys.argv[2]))
assert 'nltk' not in sys.modules
raise SystemExit(result)
'''
    result = subprocess.run([str(clean_platform_python), '-I', '-B', '-c', code,
                             str(ROOT), str(tmp_path), str(int(oracle))],
                            input=json.dumps(locomo.rows()[synthetic['task_id']]), cwd=tmp_path,
                            env={'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(tmp_path),
                                 'LANG': 'C.UTF-8', 'HG_AGENT_BACKEND': 'mock',
                                 'HG_DATASET_RUNTIME_ROOT': os.environ['HG_DATASET_RUNTIME_ROOT']},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'score    {float(oracle)}' in result.stdout


def test_unbound_local_control_preserves_legacy_interpreter(tmp_path, monkeypatch):
    spec = {'argv': ['python3', '-I', 'legacy-check.py']}
    admit = Mock(side_effect=AssertionError('unbound local control admitted a runtime'))
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    launch = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
    monkeypatch.setattr(command_control, 'subprocess', SimpleNamespace(run=launch))
    module = SimpleNamespace(VERIFY={'legacy': spec})
    assert command_control.run_check(module, {'task_id': 'legacy'}, tmp_path)[0] == -1
    assert launch.call_args.args[0] == [sys.executable, '-I', 'legacy-check.py']
    assert 'timeout' not in launch.call_args.kwargs
    admit.assert_not_called()


def test_bound_local_control_does_not_accept_a_reward_from_a_failed_check(
        synthetic, prepared_runtime, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        folder = tmp_path / 'verifier'
        folder.mkdir()
        (folder / 'reward.txt').write_text('1')
        (folder / 'evidence.json').write_text('{"measured": true}')
        return subprocess.CompletedProcess(args[0], 3, '', 'synthetic scorer failure')
    monkeypatch.setattr(command_control, 'subprocess', SimpleNamespace(run=fail))
    reward, detail = command_control.run_check(locomo, synthetic, tmp_path)
    assert reward == -1 and json.loads(detail)['measured'] is False
    assert not (tmp_path / 'verifier/reward.txt').exists()
