"""Authored MATH controls; no benchmark, model, Docker, or dependency install."""
import importlib.metadata
import json
from pathlib import Path
import importlib.util
import shutil
from types import SimpleNamespace

import pytest

from data.scaffolds.native_qa import CATALOG, Dataset
from data.scaffolds.native_qa.prepare import prepare
from harnessgrad import dataset_runtime, dataset_runtime_identity, scoring_identity, verification_context
from data.scaffolds import registry


def make_dataset(tmp_path, name):
    local = []
    for split in CATALOG[name]['splits']:
        path = tmp_path / (name + '-' + split + '.jsonl')
        path.write_text(json.dumps({'unique_id': split, 'problem': 'Find half.',
                                    'solution': r'PRIVATE REASONING \\boxed{\\frac{1}{2}}'}) + '\n')
        local.append(split + '=' + str(path))
    root = tmp_path / name
    prepare(name, root, local)
    return Dataset(name, root)


def synthetic_runtime(root):
    path = str(Path(root) / 'instances/build-a')
    return {'dataset': 'math', 'profile': 'execution', 'runtime_id': ('a' if root.endswith('a') else 'b') * 64,
            'manifest_path': path + '/artifact.json', 'path': path + '/venv',
            'python': path + '/venv/bin/python', 'runtime_paths': [path + '/venv']}


@pytest.mark.parametrize('name', ['math', 'math500'])
def test_loader_and_selected_verifier_use_shared_runtime_without_platform_package(tmp_path, monkeypatch, name):
    dataset = make_dataset(tmp_path, name)
    original = importlib.metadata.version
    def unavailable(package):
        if package == 'math-verify':
            raise AssertionError('loader inspected platform Math-Verify')
        return original(package)
    monkeypatch.setattr(importlib.metadata, 'version', unavailable)
    tasks, scorable = dataset.load()
    assert tasks and 'PRIVATE REASONING' not in json.dumps(tasks) and all(x == '' for x in scorable.values())
    assert registry.managed_runtime(name)
    root = ['/synthetic/root-a']
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda selected: synthetic_runtime(root[0]) if selected == 'math' else None)
    mapping = dataset.getattr('VERIFY')
    first = mapping[tasks[0]['task_id']]
    assert first['python_runtime']['dataset'] == 'math'
    assert first['python_runtime']['role'] == 'verifier'
    assert first['argv'][:3] == [first['python_runtime']['python'], '-I', '-B']
    assert first['result_contract']['require_exit_zero'] is True
    assert 'PRIVATE REASONING' in next(row['content'] for row in first['inputs'] if row['path'] == 'gold.json')
    root[0] = '/synthetic/root-b'
    second = mapping[tasks[0]['task_id']]
    assert first['python_runtime']['runtime_id'] != second['python_runtime']['runtime_id']
    left = dataset_runtime_identity.create(tasks={tasks[0]['task_id']: {'verifier': first['python_runtime']}})
    right = dataset_runtime_identity.create(tasks={tasks[0]['task_id']: {'verifier': second['python_runtime']}})
    assert dataset_runtime_identity.problems(left, right)
    from harnessgrad.loops.mode_a import _identical_prior
    monkeypatch.setattr(dataset_runtime_identity, 'admit', lambda value: None)
    context = verification_context.capture(tasks[:1], scorable, verifiers={tasks[0]['task_id']: first})
    point = {'identity': {'harness_sha': 'same'}, 'sampling': {'trials': 1},
             'dataset_runtime_identity': left, 'verification_context': context}
    assert _identical_prior([point], 'same', 1, dataset_runtime_identity=left, verification_context=context) is point
    assert _identical_prior([point], 'same', 1, dataset_runtime_identity=right, verification_context=context) is None
    assert _identical_prior([point], 'same', 1, dataset_runtime_identity=None, verification_context=context) is None
    with pytest.raises(ValueError, match='does not match'):
        dataset_runtime_identity.for_evaluation(left, tasks[:1], {}, {tasks[0]['task_id']: second})
    from harnessgrad.loops.eval_side import _resume_mismatch
    run = tmp_path / ('run-' + name)
    run.mkdir()
    (run / 'run_meta.json').write_text(json.dumps({'dataset_runtime_identity': left, 'verification_context': context,
                                                   'evaluated_from': {'run_id': 'source', 'round': 0}}))
    args = SimpleNamespace(dataset_runtime_identity=right, scoring_identity=None,
                           current_harness_runtime_identity=None, from_run='source', from_round=0,
                           verification_context=context)
    assert 'dataset_runtime_identity' in _resume_mismatch(run, args, '', '')
    args.dataset_runtime_identity = left
    assert _resume_mismatch(run, args, '', '') is None
    assert scoring_identity.resolve(name)['profile']['metric'] == 'math-verify-0.9.0-last-boxed'


@pytest.mark.parametrize('name', ['math', 'math500'])
def test_scorer_source_change_refuses_identity_reuse(tmp_path, monkeypatch, name):
    root = Path(__file__).resolve().parents[2]
    path = root / 'data/datasets' / name / 'scoring_profile.py'
    spec = importlib.util.spec_from_file_location('authored_math_profile', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative in (*module.SOURCES, f'data/datasets/{name}/scoring_profile.py'):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    first = scoring_identity.resolve(name)
    target = tmp_path / 'data/scaffolds/native_qa/math_grade.py'
    target.write_bytes(target.read_bytes() + b'\n# synthetic source change\n')
    second = scoring_identity.resolve(name)
    assert scoring_identity.problems(first, second)
