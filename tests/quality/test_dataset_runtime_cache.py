"""Managed artifacts remain part of cached, reused and resumed measurements."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace

import pytest

from eval.observe.tracing import _write_progress
from eval.run import runner
from harnessgrad import dataset_runtime
from harnessgrad import dataset_runtime_identity as identity
from harnessgrad import verification_context as context
from harnessgrad.loops.eval_side import (
    _baseline_block, _merge_prior, _resume_mismatch, _resume_runtime_mismatch,
)
from harnessgrad.loops.mode_a import _identical_prior


@pytest.fixture(scope='module')
def artifacts():
    if shutil.which('uv') is None:
        pytest.skip('real managed artifact checks require uv')
    root = Path(tempfile.mkdtemp(prefix='hg-cache-artifacts-', dir=Path.home() / '.cache'))
    patch = pytest.MonkeyPatch()
    try:
        source = root / 'source/data/datasets/control'
        source.mkdir(parents=True)
        (source / 'empty.lock').write_text('# Offline empty environment\n')
        minor = f'{sys.version_info.major}.{sys.version_info.minor}'
        for filename, descriptor in (
                ('execution-runtime.json', {'schema': dataset_runtime.EXECUTION_SCHEMA}),
                ('runtime.json', {'schema': dataset_runtime.SCHEMA, 'provider': 'qa'})):
            (source / filename).write_text(json.dumps({**descriptor, 'dataset': 'control',
                'python': minor, 'requirements': 'empty.lock'}))
        patch.setattr(dataset_runtime, 'ROOT', root / 'source')
        patch.setenv('UV_OFFLINE', '1')
        store = root / 'store'
        first = dataset_runtime.prepare_execution('control', python=sys.executable, root=store)
        # Publish another valid instance without deleting the previous artifact.
        next(store.glob('*/ready.json')).unlink()
        second = dataset_runtime.prepare_execution('control', python=sys.executable, root=store)
        provider = dataset_runtime.prepare('control', python=sys.executable, root=store)
        yield first, second, provider
    finally:
        patch.undo()
        dataset_runtime._remove_instance(root)


def declarations(artifacts_by_task):
    verifiers = {}
    for tid, artifact in artifacts_by_task.items():
        binding = dataset_runtime.bind(artifact, 'verifier')
        verifiers[tid] = {'kind': 'command', 'python_runtime': binding,
            'runtime_paths': binding['runtime_paths'],
            'argv': [binding['python'], '-I', '-B', 'check.py']}
    return verifiers


def runtime_identity(artifacts_by_task):
    return identity.create(tasks={tid: {'verifier': dataset_runtime.bind(artifact, 'verifier')}
                                  for tid, artifact in artifacts_by_task.items()})


@pytest.fixture
def measurement(tmp_path, monkeypatch):
    repo = tmp_path / 'harness'
    repo.mkdir()
    calls = []
    def run(_repo, task, **_options):
        calls.append(task['task_id'])
        return {'task_id': task['task_id'], 'trace': '', 'exit_code': 0, 'stderr': '',
                'verdict': {'kind': 'command', 'passed': True, 'score': 1,
                            'scorer_isolation_class': 'actor-workdir'}}
    monkeypatch.setattr(runner, 'run_one', run)
    monkeypatch.setattr(runner, 'snapshot', lambda _root: {})
    def evaluate(mapping, cache, *, value=None, progress=None):
        return runner._evaluate(repo, [{'task_id': tid} for tid in mapping], {},
            harness_sha='same-source', verifiers=declarations(mapping), cache=cache,
            dataset_runtime_identity=value, progress_dir=progress)
    return evaluate, calls, repo


def test_runtime_identity_survives_cache_and_changes_only_its_own_task(artifacts, measurement, tmp_path):
    evaluate, calls, _ = measurement
    first, second, _ = artifacts
    mapping, cache = {'a': first, 'b': first}, {}
    measured = evaluate(mapping, cache, progress=tmp_path / 'record')
    assert measured['dataset_runtime_identity'] == runtime_identity(mapping)
    for tid in mapping:
        record = json.loads((tmp_path / f'record/progress/round-0/{tid}.json').read_text())
        assert record['dataset_runtime_identity'] == identity.project(runtime_identity(mapping), [tid])
    unchanged = evaluate(mapping, cache)
    assert unchanged['cached_tasks'] == ['a', 'b'] and calls == ['a', 'b']
    changed = evaluate({'a': first, 'b': second}, cache)
    assert changed['cached_tasks'] == ['a'] and calls == ['a', 'b', 'b']


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'different'])
def test_cache_entry_requires_matching_valid_runtime_metadata(artifacts, measurement, damage):
    evaluate, calls, _ = measurement
    first, second, _ = artifacts
    cache = {}
    evaluate({'a': first}, cache)
    entry = next(iter(cache.values()))
    if damage == 'missing':
        del entry['dataset_runtime_identity']
    elif damage == 'malformed':
        entry['dataset_runtime_identity']['sha256'] = '0' * 64
    else:
        entry['dataset_runtime_identity'] = runtime_identity({'a': second})
    result = evaluate({'a': first}, cache)
    assert calls == ['a', 'a'] and result['cached_tasks'] == []


@contextmanager
def damaged(artifact):
    path = Path(artifact['path']) / 'pyvenv.cfg'
    original, mode = path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
    try:
        path.chmod(mode | stat.S_IWUSR)
        path.write_bytes(original + b'\n# corrupt fixture\n')
        path.chmod(mode)
        yield
    finally:
        path.chmod(mode | stat.S_IWUSR)
        path.write_bytes(original)
        path.chmod(mode)


def test_corrupt_current_artifact_cannot_reuse_a_cached_score(artifacts, measurement):
    evaluate, calls, _ = measurement
    first, _, _ = artifacts
    cache = {}
    evaluate({'a': first}, cache)
    with damaged(first):
        result = evaluate({'a': first}, cache)
    assert calls == ['a'] and result['cached_tasks'] == []
    assert result['per_task'] == {} and result['invalid']['a']['stage'] == 'dataset runtime identity'


def test_provider_identity_is_preserved_even_without_execution_bindings(artifacts, measurement):
    _, _, provider = artifacts
    evaluate, calls, _ = measurement
    value = identity.create(provider={'dataset': 'control', **{key: provider[key] for key in (
        'runtime_id', 'manifest_path', 'python', 'path', 'descriptor_sha256', 'requirements_sha256')}})
    cache = {}
    first = evaluate({}, cache, value=value)
    assert first['dataset_runtime_identity'] == value
    # A provider-only task remains cacheable while carrying its loading artifact.
    repo = measurement[2]
    task = [{'task_id': 'qa'}]
    for _ in range(2):
        result = runner._evaluate(repo, task, {}, cache=cache, harness_sha='same-source',
                                  dataset_runtime_identity=value)
    assert calls == ['qa'] and result['cached_tasks'] == ['qa']
    assert next(iter(cache.values()))['dataset_runtime_identity'] == value


def test_service_artifacts_and_role_changes_cannot_reuse_a_cached_score(artifacts, measurement, tmp_path):
    first, second, _ = artifacts
    _, calls, repo = measurement
    service = tmp_path / 'service.py'
    service.write_text('# Trusted fixture launch is replaced by the measurement double.\n')
    cache = {}
    def evaluate(artifact, *, verifier=False):
        binding = dataset_runtime.bind(artifact, 'service')
        return runner._evaluate(repo, [{'task_id': 'a'}], {}, cache=cache, harness_sha='same',
            setups={} if verifier else {'a': {'tool_relay': {
                'program': str(service), 'content': '{}', 'read_paths': [str(service)],
                'python': binding['python'], 'python_runtime': binding}}},
            verifiers=declarations({'a': artifact}) if verifier else {})
    evaluate(first)
    assert evaluate(first)['cached_tasks'] == ['a']
    assert evaluate(second)['cached_tasks'] == []
    # The package artifact is unchanged, but assigning it to a different role matters.
    assert evaluate(second, verifier=True)['cached_tasks'] == []
    assert calls == ['a', 'a', 'a']


def test_legacy_cache_keeps_its_key_and_shape(measurement):
    _, calls, repo = measurement
    cache = {}
    for _ in range(2):
        result = runner._evaluate(repo, [{'task_id': 'legacy'}], {}, cache=cache, harness_sha='source')
    assert calls == ['legacy'] and result['cached_tasks'] == ['legacy']
    assert len(cache) == 1 and next(iter(cache)).startswith('source:legacy:1:verification-context:')
    assert 'verification_context' in next(iter(cache.values()))
    assert 'dataset_runtime_identity' not in result
    assert 'dataset_runtime_identity' not in next(iter(cache.values()))


def test_whole_point_reuse_requires_current_valid_artifacts(artifacts):
    first, second, _ = artifacts
    value = runtime_identity({'a': first})
    ctx = context.capture([{'task_id': 'a'}], {}, verifiers=declarations({'a': first}))
    point = {'identity': {'harness_sha': 'same'}, 'sampling': {'trials': 1}, **identity.fields(value), **context.fields(ctx)}
    assert _identical_prior([point], 'same', 1, dataset_runtime_identity=value, verification_context=ctx) is point
    assert _identical_prior([point], 'same', 1,
        dataset_runtime_identity=runtime_identity({'a': second}), verification_context=ctx) is None
    assert _identical_prior([{k: v for k, v in point.items() if k != identity.KEY}],
                            'same', 1, dataset_runtime_identity=value, verification_context=ctx) is None
    with damaged(first):
        assert _identical_prior([point], 'same', 1, dataset_runtime_identity=value, verification_context=ctx) is None


def test_resume_checks_each_progress_identity_and_preserves_full_scope(artifacts, tmp_path):
    first, second, _ = artifacts
    value = runtime_identity({'a': first, 'b': first})
    ctx = context.capture([{'task_id': 'a'}, {'task_id': 'b'}], {}, verifiers=declarations({'a': first, 'b': first}))
    prior = {'a': {'complete': True, 'score': 1, 'process_outcome': 'completed',
                   **identity.fields(identity.project(value, ['a'])), **context.fields(context.project(ctx, ['a']))}}
    assert _resume_runtime_mismatch(tmp_path, prior, dataset_runtime_identity=value, verification_context=ctx) is None
    merged = _merge_prior({'per_task': {'b': 1}}, prior, dataset_runtime_identity=value)
    assert merged['per_task'] == {'a': 1, 'b': 1} and merged[identity.KEY] == value
    prior['a'][identity.KEY] = runtime_identity({'a': second})
    assert 'dataset_runtime_identity' in _resume_runtime_mismatch(
        tmp_path, prior, dataset_runtime_identity=value, verification_context=ctx)
    del prior['a'][identity.KEY]
    assert 'dataset_runtime_identity' in _resume_runtime_mismatch(
        tmp_path, prior, dataset_runtime_identity=value, verification_context=ctx)


def test_eval_metadata_and_frozen_reference_require_matching_identity(artifacts, tmp_path):
    first, second, _ = artifacts
    value, changed = runtime_identity({'a': first}), runtime_identity({'a': second})
    ctx = context.capture([{'task_id': 'a'}], {}, verifiers=declarations({'a': first}))
    meta = {'evaluated_from': {'run_id': 'train', 'round': 0}, **identity.fields(value), **context.fields(ctx)}
    (tmp_path / 'run_meta.json').write_text(json.dumps(meta))
    args = SimpleNamespace(from_run='train', from_round=0, dataset_runtime_identity=value, verification_context=ctx)
    assert _resume_mismatch(tmp_path, args, '', 'same') is None
    args.dataset_runtime_identity = changed
    assert 'dataset_runtime_identity' in _resume_mismatch(tmp_path, args, '', 'same')
    reference = tmp_path / 'reference.json'
    reference.write_text(json.dumps({'per_task': {'a': 1}, **identity.fields(value)}))
    block = _baseline_block(str(reference), {'per_task': {'a': 1}, **identity.fields(changed)})
    assert block['comparable'] is False
    assert any('dataset_runtime_identity' in error for error in block['problems'])


def test_invalid_progress_retains_the_selected_runtime_identity(artifacts, tmp_path):
    value = runtime_identity({'a': artifacts[0]})
    _write_progress(tmp_path, 0, 'a', [], {'stage': 'test', 'detail': 'authored failure'},
                    dataset_runtime_identity=value)
    record = json.loads((tmp_path / 'progress/round-0/a.json').read_text())
    assert record[identity.KEY] == value and record['invalid']['stage'] == 'test'


def test_batch_projection_validates_the_full_manifest_once(artifacts, monkeypatch):
    task_ids = [f'task-{i}' for i in range(100)]
    value = runtime_identity(dict.fromkeys(task_ids, artifacts[0]))
    validations, original = [], identity.validate
    def validate(candidate):
        validations.append(candidate)
        return original(candidate)
    with monkeypatch.context() as patch:
        patch.setattr(identity, 'validate', validate)
        projected = identity.project_many(value, task_ids)
    assert validations == [value]
    assert set(projected) == set(task_ids)
    assert all(projected[tid] == runtime_identity({tid: artifacts[0]}) for tid in task_ids)
    assert identity.project_many(None, task_ids) == dict.fromkeys(task_ids)


@pytest.mark.parametrize('reuse', [False, True])
def test_train_subset_keeps_the_full_run_identity_on_reused_and_measured_points(
        artifacts, tmp_path, monkeypatch, reuse):
    from eval.ckpt.git_state import commit_state, init_repo
    from eval.ckpt.source_tree import copy_harness
    from harnessgrad.loops import mode_a

    work = tmp_path / 'work'
    work.mkdir()
    manifest = {'name': 'probe', 'version': '1', 'entrypoint': 'agent.py'}
    (work / 'harness.json').write_text(json.dumps(manifest))
    (work / 'agent.py').write_text("print('offline fixture')\n")
    init_repo(work)
    sha = commit_state(work, 'source')
    candidate = tmp_path / 'candidate'
    copy_harness(work, candidate, ignore=shutil.ignore_patterns('.git'))
    full = runtime_identity({'t1': artifacts[0], 't2': artifacts[0]})
    task1 = {'task_id': 't1', 'goal': 'public fixture'}
    task2 = {'task_id': 't2', 'goal': 'second public fixture'}
    ctx = context.capture([task1, task2], {},
                          verifiers=declarations({'t1': artifacts[0], 't2': artifacts[0]}),
                          sandbox=False)
    point = {'round': 0, 'score': 1, 'score_kind': 'training',
             'identity': {'harness_sha': sha}, 'sampling': {'trials': 1},
             'per_task': {'t1': 1, 't2': 1}, **identity.fields(full), **context.fields(ctx)}
    method_root = tmp_path / 'method-input'
    method_root.mkdir()
    monkeypatch.setattr(mode_a.tempfile, 'mkdtemp', lambda **_kwargs: str(method_root))
    monkeypatch.setenv('HARNESSGRAD_SEED', '1')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    def submit(_work, _argv, request, *_args, **_kwargs):
        Path(request['trajectory_out']).write_text(json.dumps({
            'steps': [{'harness_dir': str(candidate), 'label': 'offline candidate'}]}))
        return {'changed': True}
    measured = []
    def evaluate(_work, tasks, _scorable, _cache, **kwargs):
        assert not reuse, 'unchanged full-plan identity should permit whole-point reuse'
        assert kwargs[identity.KEY] == full
        measured.append(tasks)
        return {'per_task': {'t1': 1}, 'trials': 1,
                **identity.fields(identity.project(full, ['t1'])),
                **context.fields(context.project(ctx, ['t1']))}
    class PointInspected(Exception):
        pass
    def inspect_point(**kwargs):
        assert kwargs[identity.KEY] == full
        raise PointInspected
    monkeypatch.setattr(mode_a, '_call_method', submit)
    monkeypatch.setattr(mode_a, 'evaluate', evaluate)
    monkeypatch.setattr(mode_a, '_curve_point', inspect_point)
    args = SimpleNamespace(method_entrypoint=['unused'], runs_root=tmp_path / 'runs',
        method_timeout=1, sandbox=False, rounds=1, jobs=1, trials=1, dataset='demo',
        baseline=None, harness='probe', side='train', sampling='all', work_root=tmp_path,
        reuse_identical=reuse, dataset_runtime_identity=full, verification_context=ctx)
    try:
        with pytest.raises(PointInspected):
            mode_a._run_mode_a(args, work, manifest, 'subset', 'A', [point], sha,
                {'traces': {}}, [task1], ['t1'], {}, {}, {},
                {'wall_clock_s': 0, 'generation_tokens': 0, 'evaluation_trials': 0,
                 'env_generation_tokens': 0}, train_ids=['t1'],
                verifiers=declarations({'t1': artifacts[0]}))
        assert bool(measured) is not reuse
    finally:
        shutil.rmtree(method_root, ignore_errors=True)
