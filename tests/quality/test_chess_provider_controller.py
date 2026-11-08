"""Synthetic provider-bound Chess controller; no data, model, or Docker."""
import json
import subprocess
import sys

import pytest

from eval.run import chess_stepwise as stepwise
from harnessgrad import dataset_runtime_identity


def identity():
    provider = {'dataset': 'chessbench_puzzles', 'runtime_id': 'a' * 64,
                'descriptor_sha256': 'b' * 64, 'requirements_sha256': 'c' * 64,
                'path': '/synthetic/artifact/venv',
                'python': '/synthetic/artifact/venv/bin/python',
                'manifest_path': '/synthetic/artifact/artifact.json'}
    return dataset_runtime_identity.create(provider)


def fixture():
    task = {'task_id': 'chessbench_puzzles/test/synthetic', 'interaction': stepwise.STEPWISE}
    gold = {'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
            'moves': ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'], 'secret': 'SECRET-GOLD'}
    return task, {'inputs': [{'path': 'gold.json', 'content': json.dumps(gold)}]}


def test_missing_identity_is_refused_without_fallback(monkeypatch):
    task, verifier = fixture()
    monkeypatch.setattr(stepwise.dataset_runtime, 'resolve', lambda *_: pytest.fail('must not reselect'))
    with pytest.raises(ValueError, match='bound provider identity'):
        stepwise.session_for(task, verifier, sandbox=True, env={'kind': 'files'})


def test_bound_provider_replays_and_admits_exact_identity(monkeypatch):
    task, verifier = fixture()
    bound = identity()
    calls = []
    def admit(name, provider):
        calls.append((name, provider))
        assert provider == bound['provider']
        return {'python': sys.executable}
    monkeypatch.setattr(stepwise.dataset_runtime, 'admit_provider', admit)
    monkeypatch.setattr(stepwise.dataset_runtime, 'resolve', lambda *_: pytest.fail('must not reselect'))
    session = stepwise.session_for(task, verifier, sandbox=True, env={'kind': 'files'},
                                  dataset_runtime_identity=bound)
    assert len(calls) == 1
    first = session.public_task()
    assert session.public_task() == first and len(calls) == 1
    second = session.submit({'uci': 'e7e5'})
    terminal = session.submit({'uci': 'b8c6'})
    assert second['solver_turn'] == 1 and terminal['status'] == 'solved'
    assert session.public_task() == terminal and len(calls) == 3
    assert all(set(o) == stepwise.PUBLIC_FIELDS for o in (first, second, terminal))
    assert 'SECRET-GOLD' not in json.dumps((first, second, terminal))


def test_worker_error_and_artifact_drift_do_not_leak_gold(monkeypatch):
    task, verifier = fixture()
    monkeypatch.setattr(stepwise.dataset_runtime, 'admit_provider',
                        lambda *_: (_ for _ in ()).throw(ValueError('SECRET-GOLD')))
    with pytest.raises(ValueError) as exc:
        stepwise.session_for(task, verifier, sandbox=True, env={'kind': 'files'},
                             dataset_runtime_identity=identity())
    assert 'SECRET-GOLD' not in str(exc.value)
    monkeypatch.setattr(stepwise.dataset_runtime, 'admit_provider', lambda *_: {'python': sys.executable})
    monkeypatch.setattr(stepwise, 'run', lambda *a, **k: subprocess.CompletedProcess([], 1, 'SECRET-GOLD', 'SECRET-GOLD'))
    with pytest.raises(ValueError) as exc:
        stepwise.session_for(task, verifier, sandbox=True, env={'kind': 'files'},
                             dataset_runtime_identity=identity())
    assert 'SECRET-GOLD' not in str(exc.value)
