"""Authored metadata only: no released benchmark data or official verifier runs."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_loader_does_not_import_numpy_or_expose_private_test_inputs(tmp_path):
    data = tmp_path / 'dataset'
    data.mkdir()
    references = sorted(p.stem for p in (ROOT / 'data/datasets/livecodebench/references').glob('*.py'))
    ids = references + [f'authored-{i}' for i in range(1055 - len(references))]
    (data / 'tests').mkdir()
    for ident in ids:
        (data / 'tests' / (ident + '.json')).write_text('{"synthetic": true}')
    with (data / 'pool.jsonl').open('w') as stream:
        stream.write(json.dumps({'problems': 1055}) + '\n')
        for index, ident in enumerate(ids):
            row = {'question_id': ident, 'question_title': 'Authored', 'question_content': 'Synthetic question',
                   'platform': 'fixture', 'contest_id': 'fixture', 'contest_date': '2020-01-01',
                   'difficulty': 'easy', 'starter_code': '', 'n_public': 1, 'n_private': 1}
            stream.write(json.dumps(row) + '\n')
    code = '''import importlib.abc, json, os, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'numpy' or fullname.startswith('numpy.'): raise AssertionError('loader imported numpy')
sys.meta_path.insert(0, Block())
from data.scaffolds import registry
tasks, scores, split = registry.load_split('livecodebench')
assert len(tasks) == len(scores) == 1055 and split is None
selected = tasks[0]['task_id']
import data.datasets.livecodebench as adapter
import hashlib
from pathlib import Path
for attr, digest_attr in [('EXECUTOR', 'EXECUTOR_SHA256'), ('PASS_K', 'PASS_K_SHA256')]:
 path = Path(os.environ['HG_DATA_LIVECODEBENCH']) / getattr(adapter, attr)
 path.parent.mkdir(parents=True, exist_ok=True)
 path.write_text('authored synthetic source ' + attr)
 setattr(adapter, digest_attr, hashlib.sha256(path.read_bytes()).hexdigest())
from harnessgrad import dataset_runtime, dataset_runtime_identity
import os
def resolve(name):
 suffix = os.environ['HG_DATASET_RUNTIME_ROOT'][-1]
 root = os.environ['HG_DATASET_RUNTIME_ROOT']+'/instances/build-'+suffix
 return {'dataset': name, 'profile': 'execution', 'runtime_id': suffix*64,
  'manifest_path': root+'/artifact.json', 'path': root+'/venv',
  'python': root+'/venv/bin/python', 'runtime_paths': [root+'/venv']}
dataset_runtime.resolve_execution = resolve
dataset_runtime_identity.admit = lambda identity: None  # synthetic artifact references
assert registry.managed_runtime('livecodebench')
identities = []
for suffix in ('a', 'b'):
 os.environ['HG_DATASET_RUNTIME_ROOT'] = '/synthetic/store-'+suffix
 tasks, setups, verifiers = registry.load_tasks('livecodebench', task_ids=[selected])
 spec = verifiers[selected]
 assert 'tests.json' not in json.dumps(setups[selected])
 assert any(row.get('path') == 'tests.json' for row in spec['inputs'])
 assert set(spec['scorer_input']['files']) == {'tests.json', 'testing_util.py', 'pass_k_utils.py'}
 assert spec['python_runtime']['runtime_id'] == suffix*64
 assert spec['result_contract']['require_exit_zero'] is True
 identity = dataset_runtime_identity.capture('livecodebench', [selected], setups, verifiers)
 assert dataset_runtime_identity._expand(identity)[selected]['verifier'] == spec['python_runtime']
 identities.append(identity['sha256'])
assert identities[0] != identities[1]
for attr in ('EXECUTOR', 'PASS_K'):
 path = Path(os.environ['HG_DATA_LIVECODEBENCH']) / getattr(adapter, attr)
 original = path.read_bytes()
 path.write_bytes(original + b' tampered')
 try:
  registry.load_tasks('livecodebench', task_ids=[selected])
  raise AssertionError('tampered scorer accepted')
 except ValueError as exc:
  assert 'pinned scorer source changed' in str(exc)
 finally:
  path.write_bytes(original)
 path.unlink()
 try:
  registry.load_tasks('livecodebench', task_ids=[selected])
  raise AssertionError('missing scorer accepted')
 except FileNotFoundError:
  pass
 finally:
  path.write_bytes(original)
assert 'numpy' not in sys.modules
print('1055 synthetic tasks loaded; numpy absent; tests private')
'''
    env = dict(os.environ, HG_DATA_LIVECODEBENCH=str(data), HG_REVIEWED_ENVIRONMENT_ONLY='1',
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'numpy absent' in result.stdout


def test_upstream_metric_helpers_keep_their_numpy_semantics(tmp_path):
    """Compare the unchanged helper functions to an independently authored NumPy calculation."""
    import ast
    import importlib.util
    if importlib.util.find_spec('numpy') is None:
        import pytest
        pytest.skip('metric control requires an already installed NumPy; no install is attempted')
    import numpy as np
    source = (ROOT / 'data/datasets/livecodebench.py').read_text()
    tree = ast.parse(source)
    helpers = [node for node in tree.body if (isinstance(node, ast.FunctionDef)
               and node.name in ('estimate_pass_at_k', 'compute_metrics_from_results'))
               or (isinstance(node, ast.ClassDef) and node.name == '_LazyNumpy')]
    namespace = {}
    exec(compile(ast.Module(body=helpers, type_ignores=[]), '<authored-metric-control>', 'exec'), namespace)
    namespace['np'] = namespace['_LazyNumpy']()
    actual = namespace['compute_metrics_from_results']({'a': [[1, 1], [1, -1]], 'b': [[1], [1]]}, k_list=[1, 2])
    assert actual['pass@1'] == 0.75
    assert actual['pass@2'] == 1.0
    assert actual['detail']['pass@1'] == {'a': 0.5, 'b': 1.0}


def test_import_does_not_read_task_helper_or_mutate_search_path(tmp_path):
    script = '''import importlib.util,sys
before=list(sys.path)
spec=importlib.util.spec_from_file_location('lcb_check_isolated_import',sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert sys.path == before
assert callable(module.main)
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
        str(ROOT/'data/datasets/livecodebench/verify/check.py')], cwd=tmp_path,
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    nonisolated = subprocess.run([sys.executable, '-B',
        str(ROOT/'data/datasets/livecodebench/verify/check.py')], cwd=tmp_path,
        capture_output=True, text=True, timeout=15)
    assert nonisolated.returncode != 0
    assert 'require Python isolated mode' in nonisolated.stderr


def test_spawn_verifier_imports_authenticated_private_sources(tmp_path):
    from data.scaffolds.scorer_input import bind
    work = tmp_path / 'work'
    work.mkdir()
    helper = work / 'data/scaffolds/scorer_input.py'
    helper.parent.mkdir(parents=True)
    helper.write_bytes((ROOT / 'data/scaffolds/scorer_input.py').read_bytes())
    (work / 'tests.json').write_text(json.dumps({'inputs': [''], 'outputs': ['5']}))
    (work / 'solution.py').write_text('print(5)\n')
    # Both modules refuse to execute from the staged cwd. run_bound must copy
    # and verify them before parent aggregation or spawned child execution.
    guard = "from pathlib import Path\nif Path(__file__).parent == Path.cwd(): raise RuntimeError('staged source executed')\n"
    (work / 'testing_util.py').write_text(guard + "def run_test(*args, **kwargs): return [True], 'private-child'\n")
    (work / 'pass_k_utils.py').write_text(guard + "def compute_metrics_from_results(*args, **kwargs): return {'pass@1': 1.0}\n")
    spec = {'inputs': [{'path': name, 'from': str(work / name)} for name in
                       ('tests.json', 'testing_util.py', 'pass_k_utils.py')], 'argv': []}
    bind(spec, 'synthetic/0', ['tests.json', 'testing_util.py', 'pass_k_utils.py'])
    launcher = tmp_path / 'launcher.py'
    launcher.write_text('import multiprocessing, sys\n'
        f'sys.path.insert(0, {str(ROOT)!r})\n'
        f'sys.path.insert(0, {str(ROOT / "data/datasets/livecodebench/verify")!r})\n'
        "import check\nif __name__ == '__main__':\n multiprocessing.set_start_method('spawn')\n raise SystemExit(check.main())\n")
    args = [sys.executable, '-I', '-B', str(launcher),
            '--tests', 'tests.json', '--solution', 'solution.py', '--reward-file', 'reward.txt',
            '--evidence-file', 'evidence.json', *spec['argv']]
    result = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (work / 'reward.txt').read_text().strip() == '1'
    evidence = json.loads((work / 'evidence.json').read_text())
    assert evidence['scorer_input']['verified'] is True
    assert evidence['per_test'] == [True]
