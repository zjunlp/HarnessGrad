"""Synthetic controller and direct-API checks; no chess data, model or services."""
import json
from types import SimpleNamespace

import pytest

from eval.run import chess_stepwise, runner
from harnessgrad import dataset_runtime, dataset_runtime_identity


def observation(status='awaiting_move', verdict='awaiting_move', turn=0):
    return {'contract': chess_stepwise.STEPWISE, 'fen': 'synthetic-fen',
            'solver_turn': turn, 'status': status, 'verdict': verdict}


def test_submission_history_is_snapshotted_and_terminal_matches_original(monkeypatch):
    histories = []
    def replay(self, history):
        histories.append(json.loads(json.dumps(history)))
        return observation('solved', 'reference_complete', 2) if len(history) == 2 else observation(turn=len(history))
    monkeypatch.setattr(chess_stepwise._ProviderSession, '_replay', replay)
    session = chess_stepwise._ProviderSession({'moves': ['synthetic-private']}, {})
    first = {'uci': 'e7e5'}
    session.submit(first)
    first['uci'] = 'mutated-by-caller'
    second = {'uci': 'b8c6'}
    result = session.submit(second)
    second['uci'] = 'also-mutated'
    assert histories[-1] == [{'uci': 'e7e5'}, {'uci': 'b8c6'}]
    assert session._history == histories[-1]
    assert result['status'] == 'solved'
    with pytest.raises(ValueError, match='puzzle session is already terminal'):
        session.submit({'uci': 'anything'})
    assert len(histories) == 3
    assert session.public_task() == result


def test_malformed_submission_never_enters_history(monkeypatch):
    monkeypatch.setattr(chess_stepwise._ProviderSession, '_replay', lambda self, h: observation())
    session = chess_stepwise._ProviderSession({}, {})
    for answer in ({'uci': float('nan')}, {'uci': 'x' * 4097}, {'uci': object()}):
        with pytest.raises(ValueError, match='invalid ChessBench submission'):
            session.submit(answer)
    assert not session._history


@pytest.mark.parametrize('response', [
    {'error': 'sensitive-worker-error'},
    {**observation(), 'verdict': 'sensitive-worker-error'},
    {**observation(), 'error': 'sensitive-worker-error'},
])
def test_controller_worker_errors_are_redacted(monkeypatch, response):
    monkeypatch.setattr(dataset_runtime, 'admit_provider', lambda *a: {'python': '/synthetic/python'})
    monkeypatch.setattr(chess_stepwise, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1 if 'error' in response else 0,
        stdout=json.dumps(response), stderr='sensitive-worker-error'))
    with pytest.raises(ValueError) as exc:
        chess_stepwise._ProviderSession({'private': 'synthetic-private'}, {})
    assert str(exc.value) == 'trusted ChessBench controller unavailable or invalid'
    assert 'sensitive-worker-error' not in str(exc.value)


def provider(tmp_path):
    instance = tmp_path / 'runtime/instance'
    venv = instance / 'venv'
    return {'dataset': 'chessbench_puzzles', 'runtime_id': 'a' * 64,
            'descriptor_sha256': 'b' * 64, 'requirements_sha256': 'c' * 64,
            'manifest_path': str(instance / 'artifact.json'), 'path': str(venv),
            'python': str(venv / 'bin/python')}


def test_direct_run_one_records_admitted_runtime_identity(monkeypatch, tmp_path):
    identity = dataset_runtime_identity.create(provider(tmp_path))
    monkeypatch.setattr(dataset_runtime, 'admit_provider', lambda *a: {})
    monkeypatch.setattr(runner.candidate_runtime, 'admit', lambda *a, **kw: None)
    monkeypatch.setattr(runner.openclaw_runtime, 'identity', lambda *a: None)
    captured = []
    def execute(*args, **kwargs):
        captured.append(kwargs['dataset_runtime_identity'])
        return {'task_id': 'chessbench_puzzles/synthetic', 'verdict': {'score': 1.0}}
    monkeypatch.setattr(runner, '_run_one', execute)
    result = runner.run_one(tmp_path, {'task_id': 'chessbench_puzzles/synthetic'},
                            setup={}, verifier={}, env={'kind': 'files'},
                            dataset_runtime_identity=identity)
    assert captured == [identity]
    assert result['dataset_runtime_identity'] == identity


def test_direct_run_one_rejects_unadmitted_identity_before_execution(monkeypatch, tmp_path):
    identity = dataset_runtime_identity.create(provider(tmp_path))
    monkeypatch.setattr(runner.candidate_runtime, 'admit', lambda *a, **kw: None)
    monkeypatch.setattr(dataset_runtime, 'admit_provider', lambda *a: (_ for _ in ()).throw(
        ValueError('synthetic runtime unavailable')))
    monkeypatch.setattr(runner, '_run_one', lambda *a, **kw: pytest.fail('actor must not start'))
    result = runner.run_one(tmp_path, {'task_id': 'chessbench_puzzles/synthetic'},
                            dataset_runtime_identity=identity)
    assert result['invalid']['stage'] == 'dataset runtime admission'
    assert 'dataset_runtime_identity' not in result
