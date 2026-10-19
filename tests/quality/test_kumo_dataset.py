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
            # Test-owned cleanup: the unchanged runner does not remove relay staging.
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
        assert relay.cleanup() is None  # original interface


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


def run_probe(synthetic, tmp_path, monkeypatch, answer='a', **kwargs):
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
    assert row['tasks'] == row['train'] + row['eval'] == 1
    assert synthetic['task_id'] in kumo.SPLIT['train'] + kumo.SPLIT['eval']
