"""Authored MATH controls; no benchmark, model, Docker, or dependency install."""
import importlib.metadata
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from data.scaffolds.native_qa import CATALOG, Dataset
from data.scaffolds.native_qa.prepare import prepare
from harnessgrad.datasets import runtime as dataset_runtime
from harnessgrad.datasets import runtime_identity as dataset_runtime_identity
from harnessgrad.provenance import measurement_components, scoring_identity, verification_context
from data.scaffolds import native_qa_provenance
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


def copy_profile_sources(destination, name):
    root = Path(__file__).resolve().parents[2]
    paths = set(native_qa_provenance._SCORER.values()) | {
        'data/scaffolds/native_qa/__init__.py', 'data/scaffolds/native_qa/prepare.py',
        f'data/datasets/{name}.py', f'data/datasets/{name}/scoring_profile.py',
        f'data/datasets/{name}/measurement_profile.py'}
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)


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
    declared = measurement_components.resolve(name)['data_source']['details']
    assert declared['revision'] == CATALOG[name]['revision']
    assert declared['declared_artifact_sha256'] is None  # authored local rows are not upstream attestation
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
    copy_profile_sources(tmp_path, name)
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(measurement_components, 'PROVIDERS', tmp_path / 'data/datasets')
    first = scoring_identity.resolve(name)
    before = measurement_components.resolve(name)
    adapter = tmp_path / f'data/datasets/{name}.py'
    adapter.write_bytes(adapter.read_bytes() + b'\n# synthetic loader change\n')
    after_loader = measurement_components.resolve(name)
    assert before['data_source'] == after_loader['data_source']
    assert before['loader'] != after_loader['loader']
    assert before['scorer'] == after_loader['scorer']
    assert scoring_identity.resolve(name) == first
    target = tmp_path / 'data/scaffolds/native_qa/math_grade.py'
    target.write_bytes(target.read_bytes() + b'\n# synthetic source change\n')
    second = scoring_identity.resolve(name)
    after_scorer = measurement_components.resolve(name)
    assert after_loader['loader'] == after_scorer['loader']
    assert after_loader['scorer'] != after_scorer['scorer']
    assert scoring_identity.problems(first, second)
    catalog = tmp_path / 'data/scaffolds/native_qa/__init__.py'
    body = catalog.read_text()
    previous_revision = before['data_source']['details']['revision']
    assert body.count(previous_revision) == 1
    catalog.write_text(body.replace(previous_revision, 'f' * 40))
    after_source = measurement_components.resolve(name)
    assert after_source['data_source'] != after_scorer['data_source']
    assert after_source['loader'] != after_scorer['loader']
    assert after_source['scorer'] == after_scorer['scorer']
    assert scoring_identity.resolve(name) == second


@pytest.mark.parametrize('relative', ['data/datasets/math.py', 'data/scaffolds/native_qa/__init__.py'])
def test_declaration_changed_during_capture_is_refused(tmp_path, monkeypatch, relative):
    copy_profile_sources(tmp_path, 'math')
    original = measurement_components.capture_implementation
    def change_before_capture(kind, root, components, **kwargs):
        if kind == 'loader':
            target = tmp_path / relative
            target.write_bytes(target.read_bytes() + b'\n# concurrent declaration change\n')
        return original(kind, root, components, **kwargs)
    monkeypatch.setattr(measurement_components, 'capture_implementation', change_before_capture)
    with pytest.raises(ValueError, match='declaration changed during capture'):
        native_qa_provenance.math_profile(tmp_path, 'math')


@pytest.mark.parametrize('damage', ['missing_dataset', 'metric_mismatch', 'invalid_syntax'])
def test_invalid_native_source_declarations_refuse_cleanly(tmp_path, damage):
    copy_profile_sources(tmp_path, 'math')
    catalog = tmp_path / 'data/scaffolds/native_qa/__init__.py'
    adapter = tmp_path / 'data/datasets/math.py'
    if damage == 'missing_dataset':
        catalog.write_text(catalog.read_text().replace("'math': {", "'missing-math': {", 1))
    elif damage == 'metric_mismatch':
        adapter.write_text(adapter.read_text().replace('math-verify-0.9.0-last-boxed', 'unexpected-metric', 1))
    else:
        adapter.write_text('this is not valid Python =')
    with pytest.raises(ValueError, match='native MATH|native QA'):
        native_qa_provenance.math_profile(tmp_path, 'math')


@pytest.mark.parametrize('name', ['math', 'math500'])
def test_historical_combined_identity_is_valid_but_not_silently_reused(name):
    root = Path(__file__).resolve().parents[2]
    previous = json.loads((root / 'tests/fixtures/math_scoring_identity/before.json').read_text())[name]
    assert scoring_identity.validate(previous) == previous
    current = scoring_identity.resolve(name)
    components = measurement_components.resolve(name)
    assert previous['profile']['metric'] == current['profile']['metric']
    assert scoring_identity.problems(previous, current)
    assert measurement_components.bundle_problems(None, components)
    assert components['data_source']['details']['declared_artifact_sha256'] is None
    measurement_components.require_scoring_alignment(current, components)
