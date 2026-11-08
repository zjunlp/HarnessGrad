"""Task editing runtime admission and default Loop offline end-to-end controls."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import venv

import pytest

from harnessgrad import dataset_runtime, dataset_runtime_identity, task_python, verification_context
from data.scaffolds import registry

ROOT = Path(__file__).resolve().parents[2]


def binding():
    root = '/synthetic/instances/control'
    return {'schema': dataset_runtime.BINDING_SCHEMA, 'dataset': 'spreadsheetbench',
            'profile': 'execution', 'role': 'actor', 'runtime_id': 'a' * 64,
            'manifest_path': root + '/artifact.json', 'path': root + '/venv',
            'python': root + '/venv/bin/python', 'runtime_paths': [root + '/venv']}


def test_role_schema_context_and_private_summary(monkeypatch):
    actor = binding()
    env = {'kind': 'files', 'network': 'none', 'task_python': actor}
    registry._check_env(env, 'control', 'authored')
    assert registry._normalize_env(env) == env
    first = verification_context.capture([{'task_id': 'control'}], {}, envs={'control': env})
    changed = copy.deepcopy(env)
    changed['task_python']['runtime_id'] = 'b' * 64
    assert verification_context.problems(first, verification_context.capture(
        [{'task_id': 'control'}], {}, envs={'control': changed}))
    admitted = []
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda item, role: admitted.append(role) or item)
    identity = dataset_runtime_identity.for_evaluation(None, [{'task_id': 'control'}], {}, {}, {'control': env})
    assert admitted == ['actor']
    assert 'actor' in identity['tasks']['control']
    assert dataset_runtime_identity.validate(identity) == identity
    old = dataset_runtime_identity.create(tasks={'control': {'verifier': {**actor, 'role': 'verifier'}}})
    assert dataset_runtime_identity.validate(old) == old
    with pytest.raises(ValueError, match='new run'):
        dataset_runtime_identity.for_evaluation(old, [{'task_id': 'control'}], {}, {}, {'control': env})
    assert set(task_python.receipt(actor)) == {'dataset', 'runtime_id', 'role', 'readonly'}
    result = task_python.environment({'PATH': '/harness/bin', 'PYTHONPATH': '/platform',
                                      'HG_AGENT_API_KEY': 'authored-local-key'}, actor)
    assert result['PATH'].split(os.pathsep)[0] == str(Path(actor['python']).parent)
    assert 'PYTHONPATH' not in result
    assert result['HG_AGENT_API_KEY'] == 'authored-local-key'


@pytest.mark.parametrize('env,sandbox,setup', [
    ({'kind': 'exec'}, True, {}), ({'kind': 'files'}, False, {}),
    ({'kind': 'files', 'backend': 'observed-v1'}, True, {}),
    ({'kind': 'files'}, True, {'workers': []})])
def test_unsupported_routes_refuse(env, sandbox, setup):
    with pytest.raises(ValueError, match='requires sandboxed files'):
        task_python.validate({**env, 'task_python': binding()}, sandbox=sandbox, setup=setup)


def test_actor_role_does_not_allow_missing_binding():
    with pytest.raises(ValueError):
        task_python.validate({'kind': 'files', 'task_python': None})
    with pytest.raises(ValueError):
        dataset_runtime.validate_binding({**binding(), 'role': 'verifier'}, 'actor')


def test_spreadsheet_env_is_lazy_and_requires_preparation(monkeypatch, tmp_path):
    from data.scaffolds.spreadsheet_runtime.actor import Environments
    envs = Environments('spreadsheetbench', lambda: [{'task_id': 'control'}])
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: (_ for _ in ()).throw(
        dataset_runtime.DatasetRuntimeUnavailable('prepare explicitly --runtime execution')))
    assert list(envs) == ['control']
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='prepare explicitly'):
        envs['control']


def test_default_loop_edits_workbooks_from_empty_platform(tmp_path):
    required = ('HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT', 'HG_SPREADSHEETBENCH2_TEST_RUNTIME_ROOT',
                'HG_SPREADSHEET_NATIVE_MANIFEST', 'HG_SPREADSHEET_ACTOR_TEST_HARNESS_ROOT')
    if any(not os.environ.get(key) for key in required):
        pytest.skip('explicit v1/v2 execution artifacts, native manifest and Loop SDK store required')
    platform_root = Path(tempfile.mkdtemp(prefix='hg-sheet-stdlib-platform-', dir=Path.home() / '.cache'))
    platform = platform_root / 'venv'
    venv.EnvBuilder(with_pip=False).create(platform)
    work = tmp_path / 'cases'
    work.mkdir()
    env = dict(os.environ,
               HG_HARNESS_RUNTIME_ROOT=os.environ[required[3]],
               HG_REVIEWED_ENVIRONMENT_ONLY='1', HG_DATA_ROOT=str(tmp_path / 'unused-data'))
    for key in list(env):
        if key.lower().endswith('_proxy'):
            env.pop(key)
    # The dataset runtime store must be outside source; tmp_path is pytest's
    # explicit external --basetemp and contains authored controls only.
    try:
        proc = subprocess.run([str(platform / 'bin/python'), '-I', '-B',
                               str(ROOT / 'tests/fixtures/spreadsheet_actor/control.py'), str(ROOT), str(work)],
                              env=env, cwd=tmp_path, capture_output=True, text=True, timeout=240)
    finally:
        shutil.rmtree(platform_root)
    (tmp_path / 'control.log').write_text(proc.stdout + proc.stderr)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith('{')]
    assert [(row['dataset'], row['score']) for row in rows] == [
        ('spreadsheetbench', 1), ('spreadsheetbench', 0),
        ('spreadsheetbench2', 1), ('spreadsheetbench2', 0)]
    assert all(row['platform_has_openpyxl'] is False for row in rows)


def test_method_round_history_and_task_context_hide_private_runtime_coordinates(tmp_path):
    from harnessgrad import channel
    actor = binding()
    identity = dataset_runtime_identity.create(tasks={'private-task-association': {'actor': actor}})
    secret_hash = 'f' * 64
    private = verification_context.create({'private-task-association': secret_hash})
    point = {'round': 0, 'score_kind': 'training', 'dataset_runtime_identity': identity,
             'verification_context': private}
    spec = {'kind': 'command', 'python_runtime': {**actor, 'role': 'verifier'},
            'argv': [actor['python'], '-I', '-B', 'check.py'],
            'inputs': [{'path': 'native-runtime.json', 'content': '{"files":{"/private/native/python":"' + secret_hash + '"}}'},
                       {'path': 'runtime-context.json', 'content': json.dumps({'python_runtime': actor})},
                       {'path': 'check.py', 'content': 'print("public scorer source")'}]}
    result = {'verdicts': {'control': {'python_runtime': {**actor, 'role': 'verifier'}}}}
    channel._stage_for_method(tmp_path, point, {'control': ''}, history=[point], task_ids=['control'],
                              tasks=[{'task_id': 'control', 'goal': 'Edit the workbook.'}],
                              setups={}, verifiers={'control': spec}, result=result)
    staged = ''.join(path.read_text() for path in (tmp_path / '_harnessgrad').rglob('*.json'))
    for sensitive in (actor['path'], actor['manifest_path'], '/private/native', secret_hash,
                      identity['sha256'], 'private-task-association', 'runtime-context.json', 'native-runtime.json'):
        assert sensitive not in staged, sensitive
    assert actor['runtime_id'] in staged
    assert 'public scorer source' in staged
    assert 'dependency-artifacts-without-task-associations' in staged
    assert point['dataset_runtime_identity'] == identity and point['verification_context'] == private


# Reuse the existing authored offline manager fixture; no registry install or
# published task artifact is modified by these admission controls.
from test_dataset_runtime import environment  # noqa: E402,F401
from test_dataset_execution_runtime import execution  # noqa: E402,F401
from test_python_execution_launch import changed_artifact  # noqa: E402


def test_actor_inventory_drift_blocks_launch_cache_h0_and_whole_round_reuse(execution, tmp_path, monkeypatch):
    from eval.run import runner
    from harnessgrad.loops.mode_a import _identical_prior
    _, _, store, _ = execution
    artifact = dataset_runtime.prepare_execution('control', root=store)
    env = {'kind': 'files', 'task_python': dataset_runtime.bind(artifact, 'actor')}
    tasks = [{'task_id': 'control'}]
    envs = {'control': env}
    identity = dataset_runtime_identity.for_evaluation(None, tasks, {}, {}, envs)
    context = verification_context.capture(tasks, {}, envs=envs)
    point = {'identity': {'harness_sha': 'same'}, 'sampling': {'trials': 1},
             'dataset_runtime_identity': identity, 'verification_context': context}
    assert _identical_prior([point], 'same', 1, dataset_runtime_identity=identity,
                            verification_context=context) is point
    repo = tmp_path / 'repo'
    repo.mkdir()
    calls, cache = [], {}
    def measure(_repo, task, **kwargs):
        calls.append(task['task_id'])
        return {'task_id': task['task_id'], 'trace': '', 'exit_code': 0, 'stderr': '',
                'verdict': {'kind': 'command', 'passed': True, 'score': 1,
                            'scorer_isolation_class': 'actor-workdir'}}
    monkeypatch.setattr(runner, 'run_one', measure)
    monkeypatch.setattr(runner, 'snapshot', lambda root: {})
    def evaluate():
        return runner._evaluate(repo, tasks, {}, cache=cache, harness_sha='same', envs=envs,
                                dataset_runtime_identity=identity)
    assert evaluate()['per_task'] == {'control': 1}
    assert evaluate()['cached_tasks'] == ['control'] and calls == ['control']
    monkeypatch.setattr(dataset_runtime_identity, 'h0_selection', lambda request:
                        (['control'], {}, {}, envs))
    monkeypatch.setattr(registry, 'provider_runtime', lambda name: None)
    monkeypatch.setattr(registry, 'provider', lambda name: None)
    assert dataset_runtime_identity._h0_preflight({'dataset': 'control'}) == identity
    with changed_artifact(artifact):
        with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='inventory'):
            task_python.admit(env)
        with pytest.raises(ValueError, match='artifact unavailable'):
            dataset_runtime_identity._h0_preflight({'dataset': 'control'})
        assert _identical_prior([point], 'same', 1, dataset_runtime_identity=identity,
                                verification_context=context) is None
        rejected = evaluate()
        assert not rejected['per_task'] and not rejected['cached_tasks']
        assert calls == ['control'], 'a cache refusal must not launch a replacement actor'
        assert 'control' in rejected['invalid']
    assert task_python.admit(env)['runtime_id'] == artifact['runtime_id']
