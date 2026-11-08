"""Ordinary resume publishes the same trial statistics as continuous execution."""
import json

import pytest

from eval.run import runner
from harnessgrad import records, verification_context
from harnessgrad.loops import eval_side
from test_repeated_trial_resume import session


def scored_session(root, patch, prefix, trials):
    root.mkdir()
    state = session.__wrapped__(root, patch)
    ids = [f'{prefix}/Query00001', f'{prefix}/Query00002']
    specs = list(state.verifiers.values())
    state.verifiers.clear()
    for task, tid, spec in zip(state.tasks, ids, specs):
        task['task_id'] = tid
        spec.pop('trial_protocol')
        spec['trial_evidence'] = {'version': 1}
        state.verifiers[tid] = spec
    state.args.sandbox = True
    state.args.dataset = 'costbench'
    state.args.trials = trials
    state.scores = dict(zip(ids, ([0, 1, 0][:trials], [1, 1, 1][:trials])))
    patch.setenv('HG_AGENT_BACKEND', 'mock')
    patch.setenv('HARNESSGRAD_WORK_ROOT', str(root / 'harness'))
    patch.setattr(eval_side, '_curve_point', records._curve_point)

    def actor(repo, task, **kwargs):
        tid = task['task_id']
        index = sum(call[0] == tid for call in state.calls)
        score = state.scores[tid][index]
        if state.verifiers[tid].get('trial_evidence'):
            trial_id = kwargs['trial_id']
        else:
            assert 'trial_id' not in kwargs
            trial_id = None
        state.calls.append((tid, trial_id, kwargs['run_seed']))
        return {'task_id': tid, 'answer': '', 'trace': 'controlled actor fixture',
                'stderr': '', 'exit_code': 0, 'env_usage': {},
                'verdict': {'kind': 'command', 'score': score, 'passed': bool(score),
                            'scorer_isolation_class': 'in-process'}}

    patch.setattr(runner, 'run_one', actor)
    # This fixture changes IDs, verifier declarations and execution policy after
    # creating the shared session. Capture the final selected tasks, not a/b.
    state.preflight()
    assert set(state.args.verification_context['tasks']) == set(ids)
    metadata = json.loads((state.run / 'run_meta.json').read_text())
    assert metadata['verification_context'] == state.args.verification_context
    return state


def curve(state):
    return json.loads((state.run / 'curve.jsonl').read_text().splitlines()[-1])


@pytest.mark.parametrize('pause', ['before_second_task', 'before_curve', 'after_curve'])
@pytest.mark.parametrize('prefix', ['costbench/test', 'costbench_dynamic/cost_change/test'])
@pytest.mark.parametrize('trials', [1, 3])
def test_resume_preserves_published_statistics(tmp_path, monkeypatch, pause, prefix, trials):
    with monkeypatch.context() as patch:
        baseline = scored_session(tmp_path / 'continuous', patch, prefix, trials)
        assert baseline.execute() == 0
        expected = curve(baseline)
        if trials > 1:
            assert expected['score_std'] == 0.235702
            assert expected['per_task_std'] == {
                f'{prefix}/Query00001': 0.471405, f'{prefix}/Query00002': 0.0}

    with monkeypatch.context() as patch:
        state = scored_session(tmp_path / 'resumed', patch, prefix, trials)
        if pause == 'before_second_task':
            state.interrupt = state.tasks[1]['task_id']
        elif pause == 'before_curve':
            def stop(*args):
                raise KeyboardInterrupt('all tasks completed before curve publication')
            patch.setattr(eval_side, '_write_curve', stop)
        if pause == 'after_curve':
            assert state.execute() == 0
        else:
            with pytest.raises(KeyboardInterrupt):
                state.execute()
        assert len(state.calls) == trials * (1 if pause == 'before_second_task' else 2)
        progress = eval_side._progress_results(state.run)
        expected_ids = [task['task_id'] for task in state.tasks]
        assert set(progress) == set(expected_ids[:1] if pause == 'before_second_task' else expected_ids)
        for tid, record in progress.items():
            assert record['verification_context'] == verification_context.project(
                state.args.verification_context, [tid])
        saved = {p: p.read_bytes() for directory in ('progress', 'trial-evidence')
                 for p in (state.run / directory).rglob('*.json')}
        state.interrupt = None
        state.args.resume = True
        patch.setattr(eval_side, '_write_curve', records._write_curve)
        assert state.execute() == 0
        assert len(state.calls) == trials * 2, 'completed trials must not be rerun'
        assert all(p.read_bytes() == body for p, body in saved.items())
        actual = curve(state)
        for key in ('score', 'per_task', 'n_trials', 'score_std', 'per_task_std',
                    'per_task_trials'):
            assert actual.get(key) == expected.get(key), key
        if trials > 1:
            assert actual['per_task_trials'] == state.scores
            assert state.results[-1]['per_task_trials'] == state.scores
        else:
            assert not {'score_std', 'per_task_std', 'per_task_trials'} & actual.keys()


def saved_evidence(state):
    paths = [state.run / 'run_meta.json', state.run / 'curve.jsonl']
    for directory in ('progress', 'trial-evidence'):
        paths.extend((state.run / directory).rglob('*.json'))
    return {path: path.read_bytes() for path in paths if path.is_file()}


def pause_after_first_task(state):
    state.interrupt = state.tasks[1]['task_id']
    with pytest.raises(KeyboardInterrupt):
        state.execute()
    assert [call[0] for call in state.calls] == [state.tasks[0]['task_id']] * state.args.trials
    assert set(eval_side._progress_results(state.run)) == {state.tasks[0]['task_id']}
    state.interrupt = None
    state.args.resume = True


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('damage', ['missing', 'stale', 'missing_selected'])
def test_ordinary_trials_require_current_selected_context(tmp_path, monkeypatch, resume, damage, capsys):
    state = scored_session(tmp_path / 'session', monkeypatch, 'costbench/test', 3)
    if resume:
        pause_after_first_task(state)
    before, calls = saved_evidence(state), list(state.calls)
    if damage == 'missing':
        del state.args.verification_context
    elif damage == 'stale':
        state.tasks[1]['goal'] = 'changed since preflight'
    else:
        state.args.verification_context = verification_context.project(
            state.args.verification_context, [state.tasks[0]['task_id']])
    assert state.execute() == 2
    assert state.calls == calls
    assert all(path.read_bytes() == body for path, body in before.items())
    expected = ('verification context missing selected task' if damage == 'missing_selected' else
                'verification context changed after preflight')
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize('archive', [False, True])
@pytest.mark.parametrize('location', ['run_meta', 'progress'])
@pytest.mark.parametrize('damage', ['missing', 'stale', 'wrong_selected'])
def test_ordinary_resume_refuses_saved_context_without_rewriting_evidence(
        tmp_path, monkeypatch, location, damage, archive, capsys):
    state = scored_session(tmp_path / 'session', monkeypatch, 'costbench_dynamic/cost_change/test', 3)
    if archive:
        pause_after_first_task(state)
    else:
        # Ordinary trials also support progress without opt-in trial archives.
        # Exercise their context gate directly; archived progress is rejected
        # earlier by its immutable archive hash when any record field changes.
        for spec in state.verifiers.values():
            spec.pop('trial_evidence')
        state.preflight()
        with monkeypatch.context() as patch:
            def stop(*args):
                raise KeyboardInterrupt('before curve publication')
            patch.setattr(eval_side, '_write_curve', stop)
            with pytest.raises(KeyboardInterrupt):
                state.execute()
        assert len(state.calls) == 6
        assert set(eval_side._progress_results(state.run)) == {t['task_id'] for t in state.tasks}
        assert not list((state.run / 'trial-evidence').rglob('*.json'))
        state.args.resume = True
    tid, other = [task['task_id'] for task in state.tasks]
    path = (state.run / 'run_meta.json' if location == 'run_meta' else
            next(p for p in (state.run / 'progress/round-0').rglob('*.json')
                 if json.loads(p.read_text())['task_id'] == tid))
    record = json.loads(path.read_text())
    if damage == 'missing':
        record.pop('verification_context')
    elif damage == 'stale':
        tasks = [dict(task) for task in state.tasks]
        tasks[0]['goal'] = 'different historical measurement'
        context = verification_context.capture(tasks, {t['task_id']: 'metric' for t in tasks},
                                               {}, state.verifiers, {}, sandbox=True)
        record['verification_context'] = (context if location == 'run_meta' else
                                          verification_context.project(context, [tid]))
    else:
        # A valid envelope for an existing but different task must not authorize
        # reuse of this completed task, even though both IDs are in the pool.
        record['verification_context'] = verification_context.project(
            state.args.verification_context, [other])
    path.write_text(json.dumps(record))
    before = saved_evidence(state)
    assert state.execute() == 2
    assert len(state.calls) == (3 if archive else 6)
    assert all(path.read_bytes() == body for path, body in before.items())
    assert not (state.run / 'curve.jsonl').exists()
    output = capsys.readouterr().err
    if archive and location == 'progress':
        assert 'ordinary trial evidence progress hash differs' in output
    else:
        assert 'verification' in output
    assert 'changed after preflight' not in output
