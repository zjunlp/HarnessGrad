"""Ordinary resume publishes the same trial statistics as continuous execution."""
import json

import pytest

from eval.run import runner
from harnessgrad import records
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
        state.calls.append((tid, kwargs['trial_id'], kwargs['run_seed']))
        return {'task_id': tid, 'answer': '', 'trace': 'controlled actor fixture',
                'stderr': '', 'exit_code': 0, 'env_usage': {},
                'verdict': {'kind': 'command', 'score': score, 'passed': bool(score),
                            'scorer_isolation_class': 'in-process'}}

    patch.setattr(runner, 'run_one', actor)
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
