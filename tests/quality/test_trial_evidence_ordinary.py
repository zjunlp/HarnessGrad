"""Archival observes ordinary trials without borrowing pass^k execution rules."""
from copy import deepcopy
import hashlib
import json

import pytest

from data.scaffolds.registry import _check_verifier
from eval.run import runner, trial_evidence
from ordinary_evidence_helpers import evaluation
from test_repeated_trial_resume import session


@pytest.mark.parametrize('outcomes', [
    ['measured'] * 3,
    ['fractional'] * 3,
    ['invalid', 'success', 'success'],
    ['measured', 'invalid', 'success'],
    ['provider', 'measured', 'measured'],
    ['measured', 'provider', 'measured'],
    ['measured', 'unmeasured', 'measured'],
])
def test_archive_keeps_ordinary_scores_seed_and_early_stop(evaluation, tmp_path, outcomes):
    execute, attempts = evaluation
    result, calls, _ = execute(outcomes, protocol=False, run_seed='ordinary-seed')
    raw_results = deepcopy(attempts)
    plain, plain_calls, _ = execute(outcomes, protocol=False, enabled=False,
                                    directory='plain', run_seed='ordinary-seed')
    index = result.pop('trial_evidence')
    assert 'repeated_trials' not in result
    assert result == plain
    assert len(calls) == len(plain_calls) == len(index['results'])
    assert [call['run_seed'] for call in calls] == ['ordinary-seed'] * len(calls)
    assert len(index['manifest']) == 3
    assert len(index['not_returned']) == 3 - len(calls)
    for i, row in enumerate(index['results']):
        path = tmp_path / 'run' / row['trial_evidence']['path']
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['trial_evidence']['sha256']
        archive = json.loads(raw)
        assert archive['result'] == raw_results[i]
        assert archive['binding']['trial_id'] == calls[i]['trial_id']
        assert archive['binding']['seed'] == 'ordinary-seed'
        assert row['status'] == 'returned'
    progress = json.loads((tmp_path / 'run/progress/round-2/ordinary/task.json').read_text())
    assert progress.pop('trial_evidence') == index['results']
    assert progress == json.loads((tmp_path / 'plain/progress/round-2/ordinary/task.json').read_text())


def test_nonprotocol_declaration_is_accepted():
    _check_verifier({'kind': 'answer', 'expected': '', 'trial_evidence': {'version': 1}}, 't', 'fixture')


def test_ordinary_archive_before_interrupt_is_preserved(evaluation, tmp_path):
    execute, _ = evaluation
    with pytest.raises(KeyboardInterrupt):
        execute(['measured', 'interrupt'], protocol=False)
    root = tmp_path / 'run/trial-evidence'
    archives = [p for p in root.rglob('*.json')
                if json.loads(p.read_text()).get('schema') == 'harnessgrad.trial-evidence.v1']
    assert len(archives) == 1
    assert list(root.rglob('in-flight/*.json'))
    assert not list(root.rglob('index.json'))


def test_ordinary_progress_write_failure_is_not_silenced(evaluation, tmp_path, monkeypatch):
    from pathlib import Path
    execute, _ = evaluation
    original = Path.write_text
    def fail(path, *args, **kwargs):
        if '/progress/' in str(path):
            raise OSError('fixture progress disk full')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(OSError, match='progress disk full'):
        execute(['measured'] * 3, protocol=False)
    assert list((tmp_path / 'run/trial-evidence').rglob('in-flight/*.json'))
    assert not list((tmp_path / 'run/trial-evidence').rglob('index.json'))


@pytest.fixture
def ordinary_session(session, monkeypatch):
    session.args.sandbox = True
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(session.run.parent / 'harness'))
    for verifier in session.verifiers.values():
        verifier.pop('trial_protocol')
        verifier['trial_evidence'] = {'version': 1}
    session.root = trial_evidence.ordinary_root(session.run, 0, session.sha)
    session.interrupt = 'b'
    with pytest.raises(KeyboardInterrupt):
        session.execute()
    assert [call[0] for call in session.calls] == ['a'] * 3
    session.interrupt = None
    session.args.resume = True
    return session


def test_ordinary_resume_validates_and_reuses_archives_and_original_seed(ordinary_session, monkeypatch):
    state = ordinary_session
    saved = {path: path.read_bytes() for path in (state.run / 'trial-evidence').rglob('*.json')}
    monkeypatch.setenv('HARNESSGRAD_SEED', 'new-driver-random-seed')
    assert state.execute() == 0
    assert [call[0] for call in state.calls] == ['a'] * 3 + ['b'] * 3
    assert {call[2] for call in state.calls} == {'0'}
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    assert 'repeated_trials' not in state.results[-1]
    assert len(state.results[-1]['trial_evidence']['results']) == 6
    assert state.execute() == 0
    assert len(state.calls) == 6


@pytest.mark.parametrize('change', ['missing', 'content', 'binding', 'progress', 'index',
                                   'removed', 'trials', 'seed', 'declaration', 'selection', 'harness', 'symlink',
                                   'published-incomplete', 'published-corrupt'])
def test_ordinary_resume_rejects_changed_evidence_before_execution(ordinary_session, change, monkeypatch):
    state = ordinary_session
    index_path = next((state.root / 'tasks').glob('*.json'))
    index = json.loads(index_path.read_text())
    reference = index['results'][0]['trial_evidence']
    archive = state.run / reference['path']
    if change == 'missing':
        archive.unlink()
    elif change == 'content':
        archive.write_text('{}')
    elif change == 'binding':
        record = json.loads(archive.read_text())
        record['binding']['seed'] = 'wrong'
        archive.write_text(json.dumps(record))
    elif change == 'progress':
        progress = state.run / 'progress/round-0/a.json'
        record = json.loads(progress.read_text()); record['score'] = .25
        progress.write_text(json.dumps(record))
    elif change == 'index':
        index_path.unlink()
    elif change == 'removed':
        for verifier in state.verifiers.values():
            verifier.pop('trial_evidence')
    elif change == 'trials':
        state.args.trials = 2
    elif change == 'seed':
        state.args.seed = 'explicit-new-seed'
        monkeypatch.setenv('HARNESSGRAD_SEED', state.args.seed)
    elif change == 'declaration':
        state.verifiers['a']['argv'] = ['different']
    elif change == 'selection':
        state.tasks = state.tasks[:1]
    elif change == 'harness':
        state.sha = 'different-harness'
    elif change == 'symlink':
        saved = state.run / 'moved-archive.json'
        archive.rename(saved); archive.symlink_to(saved)
    elif change.startswith('published-'):
        plan = json.loads((state.run / 'trial-evidence/ordinary/round-0/resume-plan.json').read_text())
        report = trial_evidence.ordinary_index(plan, index['results'])
        if change == 'published-corrupt':
            report['results'] = []
        trial_evidence.persist_ordinary(state.run, report)
    calls = list(state.calls)
    assert state.execute() == 2
    assert state.calls == calls


def test_ordinary_resume_with_only_nonarchiving_tasks_left_keeps_private_boundary(session, monkeypatch):
    session.args.sandbox = True
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(session.run.parent / 'harness'))
    for verifier in session.verifiers.values():
        verifier.pop('trial_protocol')
    session.verifiers['a']['trial_evidence'] = {'version': 1}
    calls = []
    original = runner.run_one
    def actor(repo, task, **kwargs):
        calls.append((task['task_id'], kwargs.get('_private_paths')))
        # The reused protocol fixture records an ID even for the ordinary
        # opt-out task, whose real execution correctly needs none.
        kwargs.setdefault('trial_id', 'unarchived-fixture')
        return original(repo, task, **kwargs)
    monkeypatch.setattr(runner, 'run_one', actor)
    session.interrupt = 'b'
    # Non-archiving b has no evidence marker, so interrupt through its actor.
    session.interrupt_trial = ('b', '0')
    with pytest.raises(KeyboardInterrupt):
        session.execute()
    session.interrupt_trial = None
    session.args.resume = True
    assert session.execute() == 0
    assert all(paths == (session.run / 'trial-evidence',) for _, paths in calls)
    assert {row['task_id'] for row in session.results[-1]['trial_evidence']['results']} == {'a'}


def test_published_ordinary_stop_cannot_resume_unreturned_tasks(session, monkeypatch):
    session.args.sandbox = True
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(session.run.parent / 'harness'))
    for verifier in session.verifiers.values():
        verifier.pop('trial_protocol')
        verifier['trial_evidence'] = {'version': 1}
    # Control only scheduling: exercise the real runner/task/progress/index
    # publication with the same not_run outcome as a cancelled pending task.
    def stop_remaining(tasks, measure, jobs):
        return {tasks[0]['task_id']: measure(1, tasks[0]),
                tasks[1]['task_id']: {'task_id': tasks[1]['task_id'], 'not_run': True}}
    monkeypatch.setattr(runner, '_in_parallel', stop_remaining)
    assert session.execute() == 0
    report = session.results[-1]['trial_evidence']
    assert {row['task_id'] for row in report['not_returned']} == {'b'}
    assert len(report['results']) == len(report['not_returned']) == 3
    saved = {path: path.read_bytes() for path in (session.run / 'trial-evidence').rglob('*.json')}
    assert any(path.name == 'index.json' for path in saved)
    calls = list(session.calls)
    session.args.resume = True
    assert session.execute() == 2
    assert session.calls == calls
    assert all(path.read_bytes() == body for path, body in saved.items())
