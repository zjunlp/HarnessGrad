"""Resume actual runner trial records without remeasuring or shrinking pass^k."""
import copy
import json
from types import SimpleNamespace

import pytest

from eval.run import repeated_trials, runner
from harnessgrad.loops import eval_side


@pytest.fixture
def session(tmp_path, monkeypatch):
    run = tmp_path / 'run'; run.mkdir()
    (run / 'run_meta.json').write_text(json.dumps({'evaluated_from': {'run_id': 'train', 'round': 0}}))
    work = tmp_path / 'harness'; work.mkdir()
    tasks = [{'task_id': name, 'goal': 'fixture', 'inputs': []} for name in ('a', 'b')]
    protocol = {'version': 1, 'metric': 'pass^k', 'trials': 3, 'ks': [1, 2, 3], 'seeds': [10, 20, 30]}
    verifiers = {t['task_id']: {'kind': 'command', 'argv': ['fixture'],
                              'trial_protocol': copy.deepcopy(protocol)} for t in tasks}
    args = SimpleNamespace(resume=False, from_run='train', from_round=0, trials=3,
                           jobs=1, sandbox=False, dataset='fixture', mode='A')
    state = SimpleNamespace(run=run, tasks=tasks, args=args, verifiers=verifiers,
                            calls=[], results=[], sha='harness-a', interrupt=None,
                            interrupt_trial=None, actor_exit=0)
    monkeypatch.setenv('HARNESSGRAD_SEED', '0')
    monkeypatch.setattr(eval_side, 'commit_state', lambda *a: state.sha)
    monkeypatch.setattr(eval_side, '_identity', lambda *a, **kw: {})
    monkeypatch.setattr(eval_side, '_env_record', lambda *a: {})
    monkeypatch.setattr(eval_side, '_curve_point', lambda **kw: {
        'per_task': dict(zip(kw['task_ids'], kw['scores'])), 'score_std': kw['score_std']})
    monkeypatch.setattr(eval_side, '_save_verdicts', lambda *a: None)
    monkeypatch.setattr(eval_side, '_save_traces', lambda *a, **kw: None)
    monkeypatch.setattr(eval_side.console, 'results', lambda *a, **kw: None)

    def actor(repo, task, **kwargs):
        if (task['task_id'], kwargs['run_seed']) == state.interrupt_trial:
            raise KeyboardInterrupt('controlled interruption within a task')
        state.calls.append((task['task_id'], kwargs['trial_id'], kwargs['run_seed']))
        score = int(task['task_id'] == 'b')
        return {'task_id': task['task_id'], 'answer': '', 'trace': '', 'exit_code': state.actor_exit,
                'stderr': '', 'env_usage': {}, 'verdict': {'kind': 'command', 'score': score,
                                                        'passed': bool(score)}}
    monkeypatch.setattr(runner, 'run_one', actor)
    original_start = repeated_trials.start_task
    def start(root, plan):
        if plan['manifest'][0]['task_id'] == state.interrupt:
            raise KeyboardInterrupt('controlled interruption before next task starts')
        return original_start(root, plan)
    monkeypatch.setattr(repeated_trials, 'start_task', start)

    def execute():
        return eval_side._run_eval_side(work=work, man={}, run_id='run', run_dir=run,
            tasks=state.tasks, scorable={t['task_id']: 'metric' for t in state.tasks},
            setups={}, verifiers=verifiers, envs={}, side_ids=[t['task_id'] for t in state.tasks],
            split=None, args=args, eval_from={'round': 0},
            after_evaluation=lambda result: state.results.append(result) or True)
    state.execute = execute
    state.root = repeated_trials.report_root(run, 0, state.sha)
    return state


def interrupted(session):
    session.interrupt = 'b'
    with pytest.raises(KeyboardInterrupt):
        session.execute()
    assert [c[0] for c in session.calls] == ['a'] * 3
    assert not (session.root / 'repeated-trials.json').exists()
    session.interrupt = None
    session.args.resume = True


def test_partial_resume_preserves_full_manifest_and_exact_trial_identity(session):
    interrupted(session)
    assert session.execute() == 0
    assert [c[0] for c in session.calls] == ['a'] * 3 + ['b'] * 3
    assert [c[2] for c in session.calls] == ['10', '20', '30'] * 2
    result = session.results[-1]
    report = result['repeated_trials']
    assert result['per_task'] == {'a': 0, 'b': 1}
    assert result['per_task_trials'] == {'a': [0, 0, 0], 'b': [1, 1, 1]}
    assert report['complete'] and len(report['manifest']) == len(report['results']) == 6
    assert report['metrics'] == {'1': .5, '2': .5, '3': .5}
    assert {r['trial_id'] for r in report['results']} == {
        f'round-0.harness-harness-a.trial-{i}' for i in (1, 2, 3)}


@pytest.mark.parametrize('already_published', [False, True])
def test_all_tasks_completed_resume_reuses_evidence_and_existing_report(session, monkeypatch, already_published):
    original = repeated_trials.persist
    def stop(root, report):
        if already_published:
            original(root, report)
        raise KeyboardInterrupt('stopped before curve publication')
    monkeypatch.setattr(repeated_trials, 'persist', stop)
    with pytest.raises(KeyboardInterrupt):
        session.execute()
    path = session.root / 'repeated-trials.json'
    before = path.read_bytes() if already_published else None
    monkeypatch.setattr(repeated_trials, 'persist', original)
    session.args.resume = True
    assert session.execute() == 0 and len(session.calls) == 6
    assert session.results[-1]['repeated_trials']['metrics']['3'] == .5
    if already_published:
        assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['missing', 'seed', 'score', 'harness', 'protocol', 'selection', 'declaration'])
def test_resume_refuses_missing_or_changed_trial_evidence_before_execution(session, change):
    interrupted(session)
    path = next((session.root / 'tasks').glob('*.json'))
    if change == 'missing':
        path.unlink()
    elif change in ('seed', 'score'):
        value = json.loads(path.read_text())
        value['results'][0][change] = 'wrong' if change == 'seed' else 1
        path.write_text(json.dumps(value))
    elif change == 'harness':
        session.sha = 'different-harness'
    elif change == 'protocol':
        for verifier in session.verifiers.values():
            verifier['trial_protocol']['seeds'][0] += 1
    elif change == 'selection':
        session.tasks = session.tasks[:1]
    else:
        session.verifiers['a']['argv'] = ['different-scorer']
    assert session.execute() == 2
    assert len(session.calls) == 3


def test_different_published_report_is_never_overwritten(session):
    assert session.execute() == 0
    path = session.root / 'repeated-trials.json'
    changed = json.loads(path.read_text()); changed['metrics']['1'] = .123
    path.write_text(json.dumps(changed))
    before = path.read_bytes()
    session.args.resume = True
    assert session.execute() == 2
    assert path.read_bytes() == before and len(session.calls) == 6


def test_legacy_completed_progress_without_original_trial_plan_is_refused(session):
    interrupted(session)
    (session.run / 'protocols/round-0/resume-plan.json').unlink()
    assert session.execute() == 2 and len(session.calls) == 3


def test_midtrial_interruption_never_remeasures_completed_calls(session):
    session.interrupt_trial = ('a', '20')
    with pytest.raises(KeyboardInterrupt):
        session.execute()
    assert len(session.calls) == 1
    marker = next((session.root / 'in-flight').glob('*.json'))
    assert len(json.loads(marker.read_text())['manifest']) == 3
    session.args.resume = True
    session.interrupt_trial = None
    assert session.execute() == 2 and len(session.calls) == 1
    assert not (session.root / 'repeated-trials.json').exists()


def test_resume_outer_seed_change_does_not_replace_explicit_trial_seeds(session, monkeypatch):
    interrupted(session)
    monkeypatch.setenv('HARNESSGRAD_SEED', 'new-driver-generated-seed')
    assert session.execute() == 0
    assert [c[2] for c in session.calls] == ['10', '20', '30'] * 2
    assert session.results[-1]['repeated_trials']['complete']


def test_resume_keeps_valid_scorer_result_when_actor_exited_nonzero(session):
    session.actor_exit = 1
    assert session.execute() == 0
    assert session.results[-1]['repeated_trials']['complete']
    session.args.resume = True
    assert session.execute() == 0 and len(session.calls) == 6
    result = session.results[-1]
    assert result['per_task'] == {'a': 0, 'b': 1}
    assert result['repeated_trials']['metrics'] == {'1': .5, '2': .5, '3': .5}
    assert set(result['harness_failed']) == {'a', 'b'}


def test_completion_window_before_marker_removal_fails_closed(session, monkeypatch):
    from pathlib import Path
    unlink = Path.unlink
    def crash(path, *args, **kwargs):
        if path.parent.name == 'in-flight':
            raise KeyboardInterrupt('task evidence written; marker not yet cleared')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', crash)
    with pytest.raises(KeyboardInterrupt):
        session.execute()
    assert len(session.calls) == 3
    assert eval_side._progress_results(session.run)
    monkeypatch.setattr(Path, 'unlink', unlink)
    session.args.resume = True
    assert session.execute() == 2 and len(session.calls) == 3
