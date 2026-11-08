"""KUMO uses existing setup/relay/command-verifier contracts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from data.datasets import kumo
from data.scaffolds.command_control import materialize, solve, run_check
from eval.run import tool_relay
from eval.service.tool_transport import call

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    prepared = tmp_path / 'prepared'
    folder = prepared / 'easy/TestEnv/0'
    folder.mkdir(parents=True)
    tid = 'kumo/easy/TestEnv/0'
    private = {'schema': 'kumo.case.v1', 'task_id': tid, 'source_revision': kumo.REVISION,
               'truths': ['a', 'b'], 'actions': ['probe'], 'max_queries': 50,
               'observations': {'probe': 'SECRET_OBSERVATION'}, 'valid_truth': 'a', 'seed': 0,
               'rules': {'probe': {'type': 'str', 'states': [['SECRET_OBSERVATION', ['b']]]}}}
    public = {'task_id': tid, 'truths': ['a', 'b'], 'actions': ['probe'], 'knowledge_book': 'Public rules.'}
    row = {'task_id': tid, 'path': 'easy/TestEnv/0', 'difficulty': 'easy'}
    for name, value in [('private', private), ('public', public)]:
        raw = json.dumps(value).encode()
        (folder / (name + '.json')).write_bytes(raw)
        row[name + '_sha256'] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(kumo, 'PREPARED', prepared)
    monkeypatch.setattr(kumo, 'roster', lambda: {tid: row})
    return kumo.control_task(tid)


@pytest.fixture
def relays(monkeypatch):
    handles = []
    original = tool_relay.start
    def tracked(*args, **kwargs):
        handle = original(*args, **kwargs)
        if handle is not None:
            handles.append(handle)
        return handle
    monkeypatch.setattr(tool_relay, 'start', tracked)
    yield handles
    for handle in handles:
        try:
            assert handle.proc.poll() is not None
        finally:
            # Reap every test-owned handle, including failures before runner cleanup.
            handle.stop()
            handle.cleanup()


def test_metadata_never_reads_private_case(synthetic, monkeypatch):
    original = Path.read_bytes
    def guarded(path, *args, **kwargs):
        assert path.name != 'private.json'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    tasks, _ = kumo.load()
    setup = kumo.SETUP[synthetic['task_id']]
    public = json.dumps([tasks, setup['files']])
    assert 'SECRET_OBSERVATION' not in public and 'private.json' not in public
    assert 'content_sha256' not in setup['tool_relay'] and 'record' not in setup['tool_relay']
    assert all('sha256' not in entry for entry in setup['files'])
    assert json.loads(setup['tool_relay']['content'])['path'].endswith('private.json')


def test_budget_survives_reconnects_and_receipt_is_private(synthetic, relays):
    relay = tool_relay.start(kumo.SETUP[synthetic['task_id']])
    try:
        assert call(relay.path, 'dump', {}) == {'accepted': False, 'error': 'invalid_request'}
        for step in range(1, 51):
            assert call(relay.path, 'do', {'action': 'probe'})['step'] == step
        assert call(relay.path, 'do', {'action': 'probe'})['error'] == 'query_budget_exhausted'
        relay.stop()
        assert relay.fault() is None
        assert relay.receipt['queries_used'] == 50
        assert relay.receipt['transport']['refused'] == 0
        assert 'valid_truth' not in json.dumps(relay.receipt)
    finally:
        relay.stop()
        assert relay.cleanup() is None


def test_management_calls_do_not_spend_official_budget_or_have_a_hidden_cap(synthetic):
    relay = tool_relay.start(kumo.SETUP[synthetic['task_id']])
    try:
        for _ in range(270):
            assert call(relay.path, 'stats', {})['queries_used'] == 0
        assert call(relay.path, 'do', {'action': 'probe'})['step'] == 1
        relay.stop()
        assert relay.fault() is None
        assert relay.receipt['transport']['limits']['max_calls'] is None
    finally:
        relay.cleanup()


def test_explicit_management_limit_remains_enforced(synthetic):
    setup = kumo.SETUP[synthetic['task_id']]
    setup['tool_relay']['max_calls'] = 1
    relay = tool_relay.start(setup)
    try:
        call(relay.path, 'stats', {})
        with pytest.raises(RuntimeError, match='call_budget'):
            call(relay.path, 'stats', {})
        relay.stop()
        assert relay.fault() is not None
    finally:
        relay.cleanup()


@pytest.mark.parametrize('oracle,expected', [(True, 1.0), (False, 0.0)])
def test_official_score_needs_no_query_recording(synthetic, tmp_path, oracle, expected):
    work = tmp_path / 'task'
    materialize(kumo, synthetic, work)
    if oracle:
        solve(kumo, synthetic, work)
    assert run_check(kumo, synthetic, work)[0] == expected
    (work / 'tool_recording.json').write_text('{"forged": true}')
    assert run_check(kumo, synthetic, work)[0] == expected
    evidence = json.loads((work / 'verifier/evidence.json').read_text())
    assert 'episode' not in evidence and 'queries_used' not in evidence
    # The trusted source binding is still required by the official verifier.
    private = kumo.PREPARED / kumo.roster()[synthetic['task_id']]['path'] / 'private.json'
    private.write_text('{}')
    assert run_check(kumo, synthetic, work)[0] == -1


def test_changed_private_case_is_refused(synthetic, tmp_path, monkeypatch):
    setup = kumo.SETUP[synthetic['task_id']]
    Path(json.loads(setup['tool_relay']['content'])['path']).write_text('{}')
    temp = tmp_path / 'relay-temp'
    temp.mkdir()
    monkeypatch.setattr(tool_relay.tempfile, 'tempdir', str(temp))
    with pytest.raises(RuntimeError, match='digest mismatch'):
        tool_relay.start(setup)


@pytest.mark.parametrize('oracle', [True, False])
def test_native_platform_control(synthetic, tmp_path, monkeypatch, oracle, relays):
    spec = importlib.util.spec_from_file_location('kumo_control_test', ROOT / 'data/datasets/kumo/control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    work = tmp_path / 'work'
    work.mkdir()
    assert control.run(oracle, True, synthetic['task_id'], work) == 0
    assert len(relays) == 1
    assert relays[0].receipt['queries_used'] == int(oracle)


def run_probe(synthetic, tmp_path, monkeypatch, answer='a', forge_scorer=False, **kwargs):
    from eval.run.runner import run_one
    work_root = tmp_path / 'work'
    harness = work_root / 'harness'
    harness.mkdir(parents=True)
    private = json.loads(kumo.SETUP[synthetic['task_id']]['tool_relay']['content'])['path']
    program = '''import argparse,json,pathlib,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
w=pathlib.Path(a.workdir)
assert not pathlib.Path(PRIVATE_PATH).exists()
assert not (w/'tool_recording.json').exists()
assert not (w/'kumo_verify/private.json').exists()
r=subprocess.run([sys.executable,'kumo.py','do','probe'],cwd=w,capture_output=True,text=True,check=True)
assert 'SECRET_OBSERVATION' in r.stdout
(w/'tool_recording.json').write_text('{"forged":true}')
(w/'answer.txt').write_text(ANSWER)
'''.replace('PRIVATE_PATH', repr(private)).replace('ANSWER', repr(answer))
    if forge_scorer:
        program += '''
for name in ('check.py', 'scoring.py', 'engine.py'):
    target = w/'kumo_verify'/name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("raise RuntimeError('ACTOR_SCORER')")
(w/'kumo_verify/private.json').write_text('{}')
'''
    (harness / 'agent.py').write_text(program)
    (harness / 'harness.json').write_text(json.dumps({'name': 'probe', 'entrypoint': 'agent.py',
                                                    'backend': 'cli', 'env_kinds': ['files'],
                                                    'runtime_paths': list(dict.fromkeys([sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent)]))}))
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work_root))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    return run_one(repo=harness, task=synthetic, setup=kumo.SETUP[synthetic['task_id']],
                   verifier=kumo.VERIFY[synthetic['task_id']], env=kumo.ENV[synthetic['task_id']], sandbox=True,
                   **kwargs)


@pytest.mark.parametrize('answer,expected', [('a', 1), ('b', 0)])
def test_platform_hides_private_case_and_ignores_forged_recording(synthetic, tmp_path, monkeypatch, relays, answer, expected):
    result = run_probe(synthetic, tmp_path, monkeypatch, answer)
    assert result['exit_code'] == 0, result.get('stderr')
    assert result['verdict']['score'] == expected, result
    assert 'tool_relay' not in result
    assert 'forged' not in json.dumps(result['verdict'])
    assert relays[0].receipt['queries_used'] == 1


def test_public_input_checked_before_materialization(synthetic):
    row = kumo.roster()[synthetic['task_id']]
    Path(kumo.PREPARED / row['path'] / 'public.json').write_text('{"spoofed": true}')
    with pytest.raises(ValueError, match='digest mismatch'):
        kumo.SETUP[synthetic['task_id']]


def test_registered_interaction_and_adapter_split(synthetic):
    from tools.dataset_classes import classify, ACT_VERBS
    row = classify('kumo')
    # Other adapters may add action verbs; KUMO must retain its existing route.
    assert {'do', 'call'} <= set(ACT_VERBS)
    assert row['interaction'] == 'closed_loop'
    assert row['tasks'] == 1
    # No declared division: the released task set is the benchmark's scored material, so
    # the classifier counts the whole pool on neither side (2026-10-24).
    assert kumo.SPLIT is None and kumo.SPLIT_SOURCE == 'none'
    # No declared division means no side counts; `None` is the classifier's "not loadable
    # here" (this host has no prepared KUMO payload), so both states are accepted.
    assert row['train'] in (None, 0) and row['eval'] in (None, 0)


def test_stdlib_venv_platform_controls(synthetic, tmp_path, request):
    """A package-free interpreter runs the real actor, relay and official scorer."""
    import os
    import subprocess
    import venv
    import tempfile
    runtime_dir = tempfile.TemporaryDirectory(prefix='hg-stdlib-control-', dir=Path.home())
    request.addfinalizer(runtime_dir.cleanup)
    runtime = Path(runtime_dir.name)
    venv.EnvBuilder(with_pip=False).create(runtime)
    row = kumo.roster()[synthetic['task_id']]
    script = """
import importlib.util, json, os, sys
from pathlib import Path
assert not list((Path(sys.prefix) / 'lib').glob('python*/site-packages/*'))
from data.datasets import kumo
kumo.PREPARED = Path(sys.argv[1])
row = json.loads(sys.argv[2])
kumo.roster = lambda: {row['task_id']: row}
assert len(kumo.load()[0]) == 1
spec = importlib.util.spec_from_file_location('control', kumo.ASSETS / 'control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
os.environ['HG_AGENT_BACKEND'] = 'mock'
for oracle in (True, False):
    work = Path(sys.argv[3]) / str(oracle)
    work.mkdir()
    assert control.run(oracle, True, row['task_id'], work) == 0
    assert os.environ['PATH'].split(os.pathsep)[0] == str(Path(sys.executable).absolute().parent)
"""
    result = subprocess.run([str(runtime / 'bin/python'), '-c', script,
                             str(kumo.PREPARED), json.dumps(row), str(tmp_path)],
                            cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT),
                                           'PYTHONNOUSERSITE': '1'},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unconfigured_storage_status_is_diagnostic(monkeypatch, tmp_path):
    from harnessgrad import paths
    monkeypatch.setattr(paths, '_settings', lambda: {})
    monkeypatch.setattr(kumo, 'DATASET_ROOT', paths.DatasetPath('unconfigured'))
    status = kumo.provision_status()
    assert status['present'] is False
    assert status['status'] == 'config_missing'
    assert status['root'] is None
    assert 'HG_DATA_ROOT' in status['detail']
    assert 'HG_DATA_UNCONFIGURED' in status['detail']
    assert 'DatasetPath(' not in str(status)

    # An authenticated-data failure with configured storage is not missing config.
    monkeypatch.setattr(paths, '_settings', lambda: {'HG_DATA_UNCONFIGURED': str(tmp_path)})
    def broken():
        raise ValueError('prepared artifact digest mismatch')
    monkeypatch.setattr(kumo, 'roster', broken)
    status = kumo.provision_status()
    assert status['root'] == str(tmp_path)
    assert status.get('status') != 'config_missing'
    assert status['detail'] == 'prepared artifact digest mismatch'


def test_registry_selection_reauthenticates_current_manifest(tmp_path, monkeypatch):
    from data.scaffolds import registry
    prepared = tmp_path / 'prepared'
    prepared.mkdir()
    rows = [{'task_id': f'kumo/easy/Authored/{i}', 'path': 'case'} for i in range(5290)]
    raw = json.dumps({'source_revision': kumo.REVISION, 'tasks': rows}).encode()
    manifest_path = prepared / 'manifest.json'
    manifest_path.write_bytes(raw)
    monkeypatch.setattr(kumo, 'PREPARED', prepared)
    monkeypatch.setattr(kumo, 'DATASET_ROOT', tmp_path)
    # Fixture-only pin; the adapter's released manifest pin is unchanged.
    monkeypatch.setattr(kumo, 'MANIFEST_SHA256', hashlib.sha256(raw).hexdigest())
    kumo._refresh_catalog()
    try:
        assert len(registry.load('kumo')[0]) == 5290
        manifest_path.write_bytes(raw + b' ')
        with pytest.raises(ValueError, match='manifest digest mismatch'):
            registry.load('kumo')
        status = kumo.provision_status()
        assert status['present'] is False
        assert 'manifest digest mismatch' in status['detail']
    finally:
        kumo._refresh_catalog()


@pytest.mark.parametrize('entrypoint', [
    ['-m', 'tools.prepare_kumo'],
    ['-m', 'data.datasets.personamem_v2.prepare'],
    [str(ROOT / 'data/datasets/kumo/prepare.py')],
])
def test_preparation_help_is_stdlib_and_has_no_side_effects(tmp_path, entrypoint):
    import os
    import subprocess
    result = subprocess.run([sys.executable, '-S', *entrypoint, '--help'],
                            cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(ROOT),
                                 'PYTHONDONTWRITEBYTECODE': '1'},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'usage:' in result.stdout
    assert not list(tmp_path.iterdir())


def test_completed_actor_cannot_score_after_real_relay_crash(synthetic, tmp_path, monkeypatch):
    handles = []
    completed_queries = []
    original_start = tool_relay.start
    def start(*args, **kwargs):
        handle = original_start(*args, **kwargs)
        handles.append(handle)
        original_stop = handle.stop
        def crash_on_stop():
            try:
                if handle.proc.poll() is None:
                    try:
                        completed_queries.append(call(handle.path, 'stats', {})['queries_used'])
                    finally:
                        handle.proc.kill()
                        handle.proc.wait(timeout=5)
            finally:
                original_stop()
        handle.stop = crash_on_stop
        return handle
    monkeypatch.setattr(tool_relay, 'start', start)
    result = run_probe(synthetic, tmp_path, monkeypatch, answer='a')
    assert completed_queries == [1]
    assert result.get('invalid'), result
    assert result['invalid']['stage'] == 'tool relay'
    assert (result.get('verdict') or {}).get('score') is None, result
    service = result['runtime_services']['tool_relay']
    assert service['faults'], service
    assert service['cleanup'] == {'complete': True, 'errors': []}
    assert handles[0].proc.returncode == -9
    assert not handles[0].directory.exists()


def test_real_scorer_missing_private_state_removes_previous_reward(synthetic, tmp_path):
    import shutil
    import subprocess
    spec = kumo.VERIFY[synthetic['task_id']]
    work = tmp_path / 'scorer'
    work.mkdir()
    for entry in spec['inputs']:
        target = work / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry['from'], target)
    command = [sys.executable, '-I', '-S', *spec['argv'][2:]]
    (work / 'answer.txt').write_text('a')
    correct = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=30)
    assert correct.returncode == 0, correct.stderr
    assert (work / 'verifier/reward.txt').read_text().strip() == '1'
    (work / 'answer.txt').write_text('b')
    wrong = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=30)
    assert wrong.returncode == 0, wrong.stderr
    assert (work / 'verifier/reward.txt').read_text().strip() == '0'
    (work / 'verifier/reward.txt').write_text('1\n')
    (work / 'kumo_verify/private.json').unlink()
    missing = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=30)
    assert missing.returncode == 3, missing.stderr
    assert not (work / 'verifier/reward.txt').exists()
    evidence = json.loads((work / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is False
    assert 'FileNotFoundError' in evidence['error']


def test_missing_private_state_refuses_relay_start_and_cleans_up(synthetic, tmp_path, monkeypatch):
    setup = kumo.SETUP[synthetic['task_id']]
    Path(json.loads(setup['tool_relay']['content'])['path']).unlink()
    temporary = tmp_path / 'startup'
    temporary.mkdir()
    monkeypatch.setattr(tool_relay.tempfile, 'tempdir', str(temporary))
    with pytest.raises(RuntimeError, match='FileNotFoundError'):
        tool_relay.start(setup)
    assert not list(temporary.iterdir())


def test_actor_scorer_forgery_cannot_change_official_wrong_score(synthetic, tmp_path, monkeypatch):
    result = run_probe(synthetic, tmp_path, monkeypatch, answer='b', forge_scorer=True)
    assert result['exit_code'] == 0, result
    assert result['verdict']['score'] == 0, result
    assert result['verdict']['evidence']['scorer_input']['verified'] is True
