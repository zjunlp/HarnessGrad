"""Chess-free platform acceptance with authored CSV rows and reviewed native bytes."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import venv

import pytest

from data.runtimes import NATIVE_PINS
from eval.run import chess_stepwise, runner
from harnessgrad import dataset_runtime, dataset_runtime_identity
from harnessgrad.dataset_provider import DatasetProvider, DatasetProviderError
from harnessgrad import dataset_provider
from test_python_execution_launch import changed_artifact
from test_chessbench_stepwise_runner import PREAMBLE, PLAY

ROOT = Path(__file__).resolve().parents[2]
NAME = 'chessbench_puzzles'
FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
GOLD = {'fen': FEN, 'moves': ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'],
        'answer': 'e7e5 b8c6', 'puzzle_id': 'AUTHORED_PRIVATE_ID'}


def write_csv(root, count=10000, selected=None):
    (root / '_src').mkdir(parents=True)
    with (root / '_src/puzzles.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['PuzzleId', 'Rating', 'FEN', 'Moves'])
        writer.writeheader()
        for index in range(count):
            writer.writerow({'PuzzleId': 'AUTHORED_PRIVATE_' + str(index), 'Rating': '0',
                'FEN': FEN if selected is None or index in selected else 'invalid unselected board',
                'Moves': 'e2e4 e7e5 g1f3 b8c6 f1b5'})


@pytest.fixture(scope='module')
def platform(tmp_path_factory):
    store = os.environ.get('HG_TEST_CHESS_PROVIDER_RUNTIME_ROOT')
    if not store:
        pytest.skip('set HG_TEST_CHESS_PROVIDER_RUNTIME_ROOT to a prepared disposable store')
    cache = Path.home() / '.cache'; cache.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='hg-chess-platform-', dir=cache))
    work = tmp_path_factory.mktemp('chess-platform-data')
    try:
        venv.EnvBuilder(with_pip=False, system_site_packages=False).create(directory / 'venv')
        data = work / 'data'; write_csv(data)
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(directory),
               'LANG': 'C.UTF-8', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'HG_AGENT_BACKEND': 'mock',
               'PYTHONDONTWRITEBYTECODE': '1', 'HG_DATASET_RUNTIME_ROOT': store,
               'HG_DATA_CHESSBENCH_PUZZLES': str(data)}
        if os.environ.get('HG_DATA_NATIVE_RUNTIMES'):
            env['HG_DATA_NATIVE_RUNTIMES'] = os.environ['HG_DATA_NATIVE_RUNTIMES']
        yield SimpleNamespace(python=directory / 'venv/bin/python', env=env, data=data, work=work)
    finally:
        shutil.rmtree(directory)


def cold(platform, code, *args):
    bootstrap = '''
import importlib.abc, importlib.util, json, sys
from pathlib import Path
assert importlib.util.find_spec('pip') is None
assert importlib.util.find_spec('chess') is None
sys.path.insert(0, sys.argv[1])
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'chess' or fullname.startswith('chess.') or fullname == 'data.datasets.chessbench_puzzles':
            raise AssertionError('platform imported Chess dependencies or adapter')
sys.meta_path.insert(0, Guard())
'''
    result = subprocess.run([str(platform.python), '-I', '-B', '-c', bootstrap + code,
                             str(ROOT), *map(str, args)], env=platform.env, cwd=platform.work,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_catalog_resolution_and_identity_need_no_platform_chess(platform):
    cold(platform, '''
from data.scaffolds import registry
from harnessgrad import dataset_runtime_identity as identity
from data.runtimes import NATIVE_PINS
with registry.provider_session():
    tasks, answers, split = registry.load_split('chessbench_puzzles')
    assert len(tasks) == len(answers) == 10000 and split is None
    ids = [tasks[7]['task_id']]
    selected, setups, verifiers = registry.load_tasks('chessbench_puzzles', task_ids=ids)
    value = identity.capture('chessbench_puzzles', ids, setups, verifiers)
    assert value['provider']['dataset'] == 'chessbench_puzzles'
    assert verifiers[ids[0]]['native_runtime'] == NATIVE_PINS['chessbench_puzzles']
    assert 'python_runtime' not in verifiers[ids[0]]
    assert 'AUTHORED_PRIVATE_' not in json.dumps([selected, setups])
assert 'chess' not in sys.modules
''')


def test_selected_resolve_and_control_do_not_parse_the_full_pool(platform, tmp_path):
    root = tmp_path / 'selected'; write_csv(root, count=12, selected={2, 5, 7})
    cold(platform, '''
from harnessgrad.dataset_provider import DatasetProvider
provider = DatasetProvider('chessbench_puzzles')
config = {'HG_DATA_CHESSBENCH_PUZZLES': sys.argv[2]}
ids = ['chessbench_puzzles/test/5', 'chessbench_puzzles/test/2']
selected = provider.resolve(ids, config_snapshot=config)
assert [task['task_id'] for task in selected['tasks']] == ids
assert set(selected['verifiers']) == set(ids)
assert provider.control('chessbench_puzzles/test/7', config_snapshot=config)['record']['task_id'].endswith('/7')
assert provider.resolve([], config_snapshot=config)['tasks'] == []
''', root)


@pytest.mark.parametrize('oracle', [True, False])
@pytest.mark.parametrize('sandbox', [False, True], ids=['local', 'sealed-native'])
def test_oracle_and_noop_controls_on_a_chess_free_platform(platform, tmp_path, oracle, sandbox):
    if sandbox and 'HG_DATA_NATIVE_RUNTIMES' not in platform.env:
        pytest.skip('a prepared reviewed native store is required for sealed controls')
    cold(platform, '''
from data.scaffolds.qa import control
args = ['--dataset', 'chessbench_puzzles', '--task-id', 'chessbench_puzzles/test/7',
        '--workdir', sys.argv[2], '--oracle' if sys.argv[3] == '1' else '--noop']
if sys.argv[4] == '1': args.append('--platform')
assert control.main(args) == 0
''', tmp_path / 'control', int(oracle), int(sandbox))


def test_stepwise_and_cache_use_the_exact_provider_without_host_chess(platform, tmp_path):
    repo = tmp_path / 'actor'; repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({'name': 'authored-chess-actor',
                                                  'version': '1', 'entrypoint': 'actor.py'}))
    (repo / 'actor.py').write_text(PREAMBLE + PLAY)
    cold(platform, '''
from data.scaffolds import registry
from eval.run import runner
from harnessgrad import dataset_runtime_identity as identity
with registry.provider_session():
    tasks, setups, specs = registry.load_tasks('chessbench_puzzles', task_ids=['chessbench_puzzles/test/7'])
    tasks[0]['interaction'] = 'chessbench_puzzles.stepwise.v1'
    value = identity.capture('chessbench_puzzles', [tasks[0]['task_id']], setups, specs)
cache = {}
def measure():
    return runner.evaluate(Path(sys.argv[2]), tasks, {}, setups=setups, verifiers=specs,
        sandbox=True, cache=cache, dataset_runtime_identity=value, harness_sha='authored-fixed')
first = measure()
tid = tasks[0]['task_id']
assert first['per_task'] == {tid: 1.0}, first
receipt = first['verdicts'][tid]['evidence']
assert first['verdicts'][tid]['scorer_isolation_class'] == 'in-process'
assert 'native_runtime' not in first['verdicts'][tid]
assert receipt['provider_runtime'] == value['provider']
observed = receipt['provider_observation']
assert observed['python'] == value['provider']['python']
assert observed['package_versions'] == {'chess': '1.11.2'}
assert Path(observed['package_origins']['chess']).is_relative_to(value['provider']['path'])
assert observed['isolated'] and observed['no_bytecode']
assert 'AUTHORED_PRIVATE_' not in json.dumps(receipt)
second = measure()
assert second['cached_tasks'] == [tid] and second['per_task'] == first['per_task']
assert 'chess' not in sys.modules
''', repo)


def test_preparation_check_is_readonly_and_needs_no_platform_package(platform):
    result = subprocess.run([str(platform.python), '-B', str(ROOT / 'tools/prepare_dataset.py'),
        '--dataset', NAME, '--check', '--json'], env=platform.env, cwd=platform.work,
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    checked = json.loads(result.stdout)
    assert checked['present'] and checked['tasks'] == 10000 and checked['scope'] == 'dataset-provider-load'


def test_missing_preparation_neither_imports_chess_nor_creates_a_store(platform, tmp_path):
    cold(platform, '''
import os
from harnessgrad.dataset_provider import DatasetProvider
from harnessgrad.dataset_runtime import DatasetRuntimeUnavailable
store = Path(sys.argv[2])
os.environ['HG_DATASET_RUNTIME_ROOT'] = str(store)
try:
    DatasetProvider('chessbench_puzzles').resolve(['chessbench_puzzles/test/0'])
except DatasetRuntimeUnavailable as exc:
    assert 'prepare_dataset.py' in str(exc)
else:
    raise AssertionError('unprepared provider was accepted')
assert not store.exists()
assert 'chess' not in sys.modules
''', tmp_path / 'absent-store')


@pytest.fixture
def artifact(platform, monkeypatch):
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', platform.env['HG_DATASET_RUNTIME_ROOT'])
    return dataset_runtime.resolve(NAME)


def test_controller_references_must_match_the_selected_loading_artifact(artifact):
    ref = dataset_runtime_identity.provider_reference(NAME, artifact)
    task = {'task_id': NAME + '/test/0', 'interaction': chess_stepwise.STEPWISE}
    for invalid in (None, {**ref, 'dataset': 'gsm8k'}):
        with pytest.raises(ValueError):
            chess_stepwise.bind_verifiers([task], {task['task_id']: {'provider_runtime': invalid}})
    specs = chess_stepwise.bind_verifiers([task], {}, provider=ref)
    different = {**ref, 'requirements_sha256': '0' * 64}
    with pytest.raises(ValueError, match='controller artifact differs'):
        dataset_runtime_identity.for_evaluation(
            dataset_runtime_identity.create(different), [task], {}, specs)


def test_running_controller_does_not_follow_a_changed_discovery_index(artifact):
    reference = dataset_runtime_identity.provider_reference(NAME, artifact)
    task = {'task_id': NAME + '/test/0', 'interaction': chess_stepwise.STEPWISE}
    spec = {'provider_runtime': reference, 'inputs': [{'path': 'gold.json', 'content': json.dumps(GOLD)}]}
    session = chess_stepwise.session_for(task, spec, sandbox=True, env={'kind': 'files'})
    ready = Path(artifact['manifest_path']).parents[2] / 'ready.json'
    hidden = ready.with_name('ready.hidden'); ready.rename(hidden)
    try:
        assert session.submit({'uci': 'e7e5'})['status'] == 'awaiting_move'
        assert session.submit({'uci': 'b8c6'})['status'] == 'solved'
        assert session.provider_runtime == reference
    finally:
        hidden.rename(ready)


def test_changed_provider_is_rejected_before_cache_or_actor(artifact, tmp_path, monkeypatch):
    task = {'task_id': NAME + '/test/0', 'interaction': chess_stepwise.STEPWISE, 'goal': 'authored', 'inputs': []}
    spec = {'inputs': [{'path': 'gold.json', 'content': json.dumps(GOLD)}]}
    specs = chess_stepwise.bind_verifiers([task], {task['task_id']: spec})
    repo = tmp_path / 'actor'; repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({'name': 'authored', 'version': '1', 'entrypoint': 'actor.py'}))
    (repo / 'actor.py').write_text('raise AssertionError("actor must not launch")')
    class NoRead(dict):
        def get(self, *args):
            pytest.fail('damaged provider reached measurement cache')
    monkeypatch.setattr(runner, 'run_one', lambda *a, **k: pytest.fail('damaged provider launched actor'))
    with changed_artifact(artifact):
        result = runner.evaluate(repo, [task], {}, verifiers=specs, cache=NoRead({'authored': 1}), sandbox=True)
    assert not result['per_task'] and not result['cached_tasks'] and result['invalid']


@pytest.mark.parametrize('payload', [None, {}, {'contract': 'x', 'gold': {}, 'answers': [], 'credentials': 'forbidden'},
    {'contract': 'x', 'gold': {}, 'answers': 'not a list'}])
def test_stepwise_request_rejects_malformed_payloads(artifact, payload):
    with pytest.raises(DatasetProviderError):
        DatasetProvider(NAME).stepwise(payload)


def test_reviewed_adapter_and_native_pin_were_not_rewritten():
    manifest_path = ROOT / NATIVE_PINS[NAME]['manifest']
    raw = manifest_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == NATIVE_PINS[NAME]['sha256']
    manifest = json.loads(raw)
    assert hashlib.sha256((ROOT / 'data/datasets/chessbench_puzzles.py').read_bytes()).hexdigest() == \
        manifest['files']['/scorer/data/datasets/chessbench_puzzles.py']['sha256']


@pytest.mark.parametrize('stop', [True, False], ids=['relay-stop', 'absolute-deadline'])
def test_stepwise_worker_is_bounded_and_reaped(artifact, monkeypatch, stop):
    ref = dataset_runtime_identity.provider_reference(NAME, artifact)
    task = {'task_id': NAME + '/test/0', 'interaction': chess_stepwise.STEPWISE}
    spec = {'provider_runtime': ref, 'inputs': [{'path': 'gold.json', 'content': json.dumps(GOLD)}]}
    session = chess_stepwise.session_for(task, spec, sandbox=True, env={'kind': 'files'})
    monkeypatch.setattr(dataset_provider, '_BOOTSTRAP', 'import time; time.sleep(60)')
    failures = []
    def submit():
        try:
            session.submit({'uci': 'e7e5'})
        except ValueError as exc:
            failures.append(str(exc))
    session.set_deadline(time.monotonic() + (5 if stop else .4))
    worker = threading.Thread(target=submit)
    worker.start()
    child = None
    try:
        end = time.monotonic() + 3
        while time.monotonic() < end and child is None:
            child = session.process
            time.sleep(.005)
        assert child is not None, 'controlled sleeper did not launch'
        if stop:
            session.stop()
        worker.join(timeout=2)
        assert not worker.is_alive() and failures
        assert child.poll() is not None and not Path('/proc', str(child.pid)).exists()
        assert session.process is None
    finally:
        session.stop()
        worker.join(timeout=5)
