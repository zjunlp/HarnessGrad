"""Operator limits separate cached measurements even without a result contract."""
import json

from eval.run import runner


def test_policy_changes_do_not_reuse_legacy_or_other_policy_scores(tmp_path, monkeypatch):
    cache = {}
    calls = []
    tid = 'kumo/cache-control'

    def measured(*args, **kwargs):
        calls.append(None)
        score = float(len(calls) % 2)
        return {'verdict': {'kind': 'command', 'score': score, 'passed': bool(score),
                            'scorer_isolation_class': 'actor-workdir'},
                'trace': '', 'exit_code': 0, 'stderr': ''}

    monkeypatch.setattr(runner, 'run_one', measured)

    def evaluate(policy):
        if policy is None:
            monkeypatch.delenv('HG_EVAL_LIMITS', raising=False)
        else:
            monkeypatch.setenv('HG_EVAL_LIMITS', json.dumps(policy))
        return runner._evaluate(tmp_path, [{'task_id': tid}], {}, harness_sha='same',
                                 cache=cache, verifiers={tid: {'kind': 'command'}})

    legacy = evaluate(None)
    assert len(calls) == 1 and 'same:kumo/cache-control:1' in cache
    assert evaluate({})['cached_tasks'] == [tid]
    first_policy = {'task': {'agent_timeout_s': 10, 'verify_timeout_s': 20}}
    changed = evaluate(first_policy)
    assert len(calls) == 2 and not changed['cached_tasks']
    assert changed['per_task'][tid] != legacy['per_task'][tid]
    reordered = {'task': {'verify_timeout_s': 20, 'agent_timeout_s': 10}}
    assert evaluate(reordered)['cached_tasks'] == [tid] and len(calls) == 2
    assert not evaluate({'task': {'agent_timeout_s': None, 'verify_timeout_s': 20}})['cached_tasks']
    assert len(calls) == 3
    assert evaluate(None)['per_task'] == legacy['per_task']
    assert len(calls) == 3
