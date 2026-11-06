"""Offline trial reservation tests; the driver is never executed."""
import importlib.util
import multiprocessing
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location('trial_boundary_server', ROOT / 'console/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def trial(tmp_path, monkeypatch):
    server = load()
    candidates = tmp_path / 'candidates'
    (candidates / 'safe').mkdir(parents=True)
    (candidates / 'safe' / 'harness.json').write_text('{}')
    runs = tmp_path / 'runs'
    runs.mkdir()
    monkeypatch.setattr(server, 'CANDIDATES', candidates)
    monkeypatch.setenv('HG_CONSOLE_TRIAL_RUNS', str(runs))
    return server, runs


def test_valid_and_no_overwrite(trial, monkeypatch):
    server, runs = trial
    calls = []
    def fake(argv, **kwargs):
        calls.append(argv)
        assert (runs / 'one').is_dir() and (runs / 'work/one').is_dir()
        (runs / 'one/curve.jsonl').write_text('{"score": 0.75}\n')
        (runs / 'work/one/keep').write_text('history')
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')
    monkeypatch.setattr(server.subprocess, 'run', fake)
    status, result = server.harness_candidate_trial('safe', 'example', 'one')
    assert status == 200 and result['score'] == .75 and result['stdout_tail'] == 'ok'
    assert calls[0][calls[0].index('--dataset') + 1] == 'example'
    assert calls[0][calls[0].index('--sampling') + 1] == 'all'
    assert calls[0][calls[0].index('--rounds') + 1] == '0'
    assert calls[0][calls[0].index('--work-root') + 1] == str(runs / 'work')
    assert server.harness_candidate_trial('safe', 'example', 'one')[0] == 409
    assert (runs / 'work/one/keep').read_text() == 'history'
    assert len(calls) == 1


@pytest.mark.parametrize('name', ['.', '..', '/absolute', '../escape', 'work', 'a/b'])
def test_bad_run_id(trial, name):
    server, runs = trial
    assert server.harness_candidate_trial('safe', 'example', name)[0] == 400
    assert not list(runs.iterdir())


def test_candidate_and_config_preflight(trial, monkeypatch):
    server, runs = trial
    assert server.harness_candidate_trial('..', 'example', 'x')[0] == 404
    monkeypatch.delenv('HG_CONSOLE_TRIAL_RUNS')
    assert server.harness_candidate_trial('safe', 'example', 'x')[0] == 503
    monkeypatch.setenv('HG_CONSOLE_TRIAL_RUNS', str(ROOT / 'runs'))
    assert server.harness_candidate_trial('safe', 'example', 'x')[0] == 503
    assert not list(runs.iterdir())


def test_work_symlink_and_existing_workspace(trial):
    server, runs = trial
    outside = runs.parent / 'outside'
    outside.mkdir()
    (runs / 'work').symlink_to(outside, target_is_directory=True)
    assert server.harness_candidate_trial('safe', 'example', 'x')[0] == 503
    assert not list(outside.iterdir()) and not (runs / 'x').exists()
    (runs / 'work').unlink()
    (runs / 'work/x').mkdir(parents=True)
    (runs / 'work/x/keep').write_text('historical')
    assert server.harness_candidate_trial('safe', 'example', 'x')[0] == 409
    assert not (runs / 'x').exists()
    assert (runs / 'work/x/keep').read_text() == 'historical'


def test_driver_failure_and_timeout_preserve_reservations(trial, monkeypatch):
    server, runs = trial
    def fail(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 1800)
    monkeypatch.setattr(server.subprocess, 'run', fail)
    assert server.harness_candidate_trial('safe', 'example', 'x')[0] == 504
    assert (runs / 'x').is_dir() and (runs / 'work/x').is_dir()
    monkeypatch.setattr(server.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=2, stdout='', stderr='failed'))
    status, result = server.harness_candidate_trial('safe', 'example', 'y')
    assert status == 422 and result['stderr_tail'] == 'failed'
    assert (runs / 'y').is_dir() and (runs / 'work/y').is_dir()
    def cannot_launch(*a, **kw):
        raise OSError('synthetic launch failure')
    monkeypatch.setattr(server.subprocess, 'run', cannot_launch)
    status, result = server.harness_candidate_trial('safe', 'example', 'z')
    assert status == 503 and result == {'error': 'trial-launch-failed', 'run_id': 'z'}
    assert (runs / 'z').is_dir() and (runs / 'work/z').is_dir()
    assert server.harness_candidate_trial('safe', 'example', 'z')[0] == 409


def race_worker(candidates, runs, gate, queue):
    server = load()
    server.CANDIDATES = Path(candidates)
    os.environ['HG_CONSOLE_TRIAL_RUNS'] = runs
    server.subprocess.run = lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr='')
    gate.wait()
    queue.put(server.harness_candidate_trial('safe', 'example', 'race')[0])


def test_parallel_process_reservation(trial):
    _, runs = trial
    ctx = multiprocessing.get_context('fork')
    gate, queue = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=race_worker, args=(str(runs.parent / 'candidates'), str(runs), gate, queue)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert sorted(queue.get(timeout=2) for _ in processes) == [200, 409]
