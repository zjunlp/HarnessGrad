"""Actor payload purity/privacy and canonical backend forwarding; all data authored."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from eval.run import actor_environment, actor_spec, actor_execution, runner


def sandbox_spec(tmp_path, environment):
    return actor_spec.sandbox(['/actor/python', '/actor/agent.py'], platform=tmp_path / 'platform',
        work_root=tmp_path / 'work', task_dir=tmp_path / 'task', environment=environment,
        timeout_s=7, repo=tmp_path / 'repo', state_dir=tmp_path / 'state',
        runtime_paths=(tmp_path / 'actor-deps',), limits={'memory_mb': 128},
        stepwise_dir=tmp_path / 'public-socket', surfaced_runtime=(tmp_path / 'actor-runtime',),
        network='none', model_socket=tmp_path / 'model.sock', model_path='/v1',
        tool_socket=None, package_overlays=(), immutable_runtimes=((tmp_path / 'public-lib', 'lib'),),
        managed=True, private_paths=(tmp_path / 'private-archive',))


def test_builders_do_not_allocate_and_accept_only_actor_payload(tmp_path, monkeypatch):
    before = list(tmp_path.iterdir())
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **kw: pytest.fail('builder allocated a directory'))
    env = {'HG_AGENT_API_KEY': 'actor-only'}
    spec = sandbox_spec(tmp_path, env)
    assert list(tmp_path.iterdir()) == before
    assert spec.options['visible_paths'] == (tmp_path / 'public-socket', tmp_path / 'actor-runtime')
    assert spec.options['private_paths'] == (tmp_path / 'private-archive',)
    assert spec.options['platform_interpreter'] is False
    assert 'verifier' not in spec.options and 'service_specs' not in spec.options
    with pytest.raises(TypeError):
        actor_spec.process([], timeout_s=7, cwd=tmp_path, environment=env,
                           verifier={'expected': 'private gold'})


def test_actor_environment_keeps_role_and_python_projection_without_mutating_input(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, '_improver_credentials', lambda: {'IMPROVER_SENTINEL'})
    for key, value in {'HG_AGENT_API_KEY': 'actor', 'HG_ENV_API_KEY': 'environment-secret',
        'HG_JUDGE_API_KEY': 'scorer-secret', 'HG_METHOD_API_KEY': 'method-secret',
        'HARNESSGRAD_SEED': 'service-seed', 'IMPROVER_SENTINEL': 'improver-secret'}.items():
        monkeypatch.setenv(key, value)
    filtered = runner.harness_env()
    assert filtered['HG_AGENT_API_KEY'] == 'actor'
    assert not {'HG_ENV_API_KEY', 'HG_JUDGE_API_KEY', 'HG_METHOD_API_KEY',
                'HARNESSGRAD_SEED', 'IMPROVER_SENTINEL'} & filtered.keys()
    base = {'PATH': '/task/bin', 'PYTHONPATH': 'ambient', 'HG_AGENT_API_KEY': 'actor'}
    before = dict(base)
    home = tmp_path / 'not-created'
    native = actor_environment.native_home(base, home)
    staged = actor_environment.with_task_state(native, bun_environment={'BUN_TEST': 'selected'},
        worker_environment={'HG_WORKER_ID': 'peer'}, deadline_s=7, state_path='/state')
    artifact = {'python': '/actor/bin/python', 'path': '/actor'}
    final = actor_environment.with_python(staged, overlay_environment={'PYTHONPATH': 'overlay'},
        managed=True, managed_artifact=artifact, is_exec=False, task_artifact=None)
    assert base == before and not home.exists()
    assert final['HG_WORKER_ID'] == 'peer' and final['BUN_TEST'] == 'selected'
    assert final['HG_AGENT_DEADLINE_S'] == '7' and final['HARNESSGRAD_STATE'] == '/state'
    assert 'PYTHONPATH' not in final and final['VIRTUAL_ENV'] == '/actor'


@pytest.mark.parametrize('backend', ['process', 'container', 'sandbox'])
def test_execution_forwards_exact_payload_and_returns_or_raises_same_object(tmp_path, backend):
    spec = sandbox_spec(tmp_path, {'HG_AGENT_MODEL': 'authored'})
    spec = actor_spec.ActorLaunchSpec(backend, spec.argv, spec.options)
    calls = []
    result = SimpleNamespace(returncode=0, stderr='', stdout='authored')

    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        return result

    ports = {name + '_run': launch for name in ['process', 'container', 'sandbox']}
    assert actor_execution.execute(spec, **ports) is result
    assert calls == [(spec.argv, spec.options)]
    failure = subprocess.TimeoutExpired(spec.argv, 7)

    def fail(*a, **kw):
        raise failure

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        actor_execution.execute(spec, **{name + '_run': fail for name in ['process', 'container', 'sandbox']})
    assert raised.value is failure


@pytest.mark.parametrize('backend', ['process', 'container', 'sandbox'])
def test_runner_serialized_actor_payload_excludes_private_verification_inputs(tmp_path, monkeypatch, backend):
    repo = tmp_path / 'work/workspace'
    repo.mkdir(parents=True)
    (repo / 'harness.json').write_text('{"entrypoint":"agent.py"}')
    (repo / 'agent.py').write_text('# authored backend control\n')
    private = tmp_path / 'private-scorer'
    private.mkdir()
    (private / 'dependency.py').write_text('SCORER_DEPENDENCY_SENTINEL')
    captured = []
    original_execute = actor_execution.execute
    interesting = {'HG_AGENT_API_KEY', 'HG_JUDGE_API_KEY', 'HG_ENV_API_KEY', 'HG_METHOD_API_KEY'}

    def inspect(spec, **ports):
        public = asdict(spec)
        public['options']['env'] = {key: value for key, value in spec.options['env'].items() if key in interesting}
        captured.append(public)
        return original_execute(spec, **ports)

    def launch(argv, **options):
        workdir = Path(options.get('task_dir') or options['cwd'])
        (workdir / 'answer.txt').write_text('ok')
        (workdir / 'trace.jsonl').write_text('{"usage":{"calls":0}}\n')
        return SimpleNamespace(returncode=0, stderr='', stdout='')

    monkeypatch.setattr(actor_execution, 'execute', inspect)
    monkeypatch.setattr(runner, 'run_process', launch)
    monkeypatch.setattr(runner.sandbox_mod, 'run', launch)
    monkeypatch.setattr(runner.container_mod, 'run', launch)
    monkeypatch.setattr(runner.container_mod, 'image_python', lambda image: '/image/python')
    monkeypatch.setattr(runner.harness_runtime, 'lookup', lambda *a, **kw: None)
    monkeypatch.setattr(runner.harness_runtime, 'interpreter_version', lambda *a, **kw: 'authored')
    monkeypatch.setattr(runner, '_verify', lambda *a, **kw: {'passed': True, 'score': 1})
    # Payload-only control: no real observer/container is claimed by this fake backend.
    monkeypatch.setattr(runner, '_refuse_unobserved_policies', lambda *a, **kw: None)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path / 'work'))
    for key, value in {'HG_AGENT_API_KEY': 'ACTOR_ONLY', 'HG_JUDGE_API_KEY': 'SCORER_CREDENTIAL_SENTINEL',
                       'HG_ENV_API_KEY': 'SERVICE_CREDENTIAL_SENTINEL', 'HG_METHOD_API_KEY': 'METHOD_SECRET'}.items():
        monkeypatch.setenv(key, value)
    # Immutable verifier inputs are real and privately validated, never actor binds.
    verifier = {'kind': 'answer', 'expected': 'GOLD_CONTENT_SENTINEL'}
    if backend != 'process':
        verifier['inputs'] = [{'path': 'private-lib', 'from': str(private), 'immutable': True}]
    env = {'kind': 'exec', 'image': 'authored:fixed'} if backend == 'container' else {'kind': 'files'}
    result = runner.run_one(repo, {'task_id': 'authored', 'goal': 'public goal'},
        sandbox=backend != 'process', env=env, verifier=verifier)
    assert not result.get('invalid'), result.get('invalid')
    assert result['verdict']['passed'] and len(captured) == 1
    payload = json.dumps(captured, default=str)
    for sentinel in ('GOLD_CONTENT_SENTINEL', 'SCORER_CREDENTIAL_SENTINEL', 'SCORER_DEPENDENCY_SENTINEL',
                     'SERVICE_CREDENTIAL_SENTINEL', str(private)):
        assert sentinel not in payload
    assert captured[0]['backend'] == backend
    assert captured[0]['options']['env']['HG_AGENT_API_KEY'] == 'ACTOR_ONLY'



def test_credential_port_remains_per_key_and_skips_private_role_prefixes(monkeypatch):
    environment = {'A': 'actor-a', 'HG_METHOD_API_KEY': 'method', 'B': 'actor-b',
                   'HG_ENV_API_KEY': 'environment', 'HG_JUDGE_API_KEY': 'judge',
                   'HARNESSGRAD_SEED': 'service-seed', 'C': 'actor-c'}
    monkeypatch.setattr(actor_environment.os, 'environ', environment)
    calls = []

    def credential_names():
        # A snapshot would only remove A. The original comprehension invokes
        # the current lookup separately for each eligible key.
        key = ('A', 'B', 'C')[len(calls)]
        calls.append(key)
        return {key}

    assert actor_environment.harness_env(credential_names=credential_names) == {}
    assert calls == ['A', 'B', 'C']


def test_public_delegate_honors_legacy_private_rebinding_between_keys(monkeypatch):
    monkeypatch.setattr(actor_environment.os, 'environ', {'A': 'a', 'HG_METHOD_API_KEY': 'm', 'B': 'b', 'C': 'c'})
    calls = []

    def later():
        calls.append('later')
        return {'B'}

    def first():
        calls.append('first')
        monkeypatch.setattr(runner, '_improver_credentials', later)
        return {'A'}

    monkeypatch.setattr(runner, '_improver_credentials', first)
    assert runner.harness_env() == {'C': 'c'}
    assert calls == ['first', 'later', 'later']
