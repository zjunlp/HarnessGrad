"""A failed declared trial must not cancel or relabel independent later trials."""
import json

import pytest

from eval.run import repeated_trials, runner


PROTOCOL = {'version': 1, 'metric': 'pass^k', 'trials': 3,
            'ks': [1, 2, 3], 'seeds': [10, 20, 30]}


def run_trials(tmp_path, monkeypatch, outcomes, *, declared=True):
    calls, attempts, cache = [], [], {}

    def actor(_repo, _task, **kwargs):
        index = len(calls)
        calls.append(kwargs)
        seed = kwargs['run_seed']
        score = int(index != 1)  # The middle measured trial is a genuine zero.
        result = {'exit_code': 0, 'stderr': f'stderr for seed {seed}',
                  'trace': json.dumps({'seed': seed, 'trial_id': kwargs.get('trial_id')}) + '\n',
                  'verdict': {'score': score, 'passed': bool(score),
                              'scorer_isolation_class': 'actor-workdir'},
                  'harness_runtime': {'fixture_seed': seed},
                  'runtime_services': {'fixture_seed': seed}}
        if outcomes[index] == 'invalid':
            result['invalid'] = {'stage': 'tool relay', 'detail': f'relay failed for seed {seed}'}
        elif outcomes[index] == 'unmeasured':
            result['verdict'] = {'unmeasured': f'scorer failed for seed {seed}'}
        elif outcomes[index] == 'provider':
            result.update(exit_code=1, stderr=f'APIConnectionError: Connection error for seed {seed}',
                          verdict={'score': 0, 'passed': False})
        attempts.append(result)
        return result

    monkeypatch.setattr(runner, 'run_one', actor)
    result = runner._evaluate(tmp_path, [{'task_id': 't'}], {}, trials=3,
                              cache=cache, run_id='fixture-run', run_seed='outer-seed',
                              round_no=2, harness_sha='fixture-sha',
                              progress_dir=tmp_path / 'records',
                              verifiers={'t': {'trial_protocol': PROTOCOL}} if declared else {})
    return result, calls, attempts, cache


@pytest.mark.parametrize('failure', ['invalid', 'unmeasured', 'provider'])
@pytest.mark.parametrize('failed_index', [0, 1, 2], ids=['first', 'middle', 'last'])
def test_failed_declared_trial_does_not_cancel_other_trials(tmp_path, monkeypatch, failure, failed_index):
    outcomes = ['measured'] * 3
    outcomes[failed_index] = failure
    result, calls, attempts, cache = run_trials(tmp_path, monkeypatch, outcomes)

    assert [call['run_seed'] for call in calls] == ['10', '20', '30']
    assert [call['trial_id'] for call in calls] == [
        f'round-2.harness-fixture-sha.trial-{i}' for i in (1, 2, 3)]
    assert cache == {}
    report = result['repeated_trials']
    assert len(report['manifest']) == len(report['results']) == 3
    assert report['missing'] == []
    assert len(report['invalid']) == 1
    assert report['complete'] is False and report['metrics'] is None
    assert report['per_task'] is None
    for index, row in enumerate(report['results']):
        assert row['seed'] == calls[index]['run_seed']
        assert row['trial_id'] == calls[index]['trial_id']
        assert row['runtime_services'] == attempts[index]['runtime_services']
        if index == failed_index:
            assert row['status'] == 'unmeasured' and row['score'] is None
            assert f'seed {row["seed"]}' in json.dumps(row['reason'])
        else:
            assert row['status'] == 'measured'
            assert row['score'] == int(index != 1)
            assert row['reason'] is None

    root = repeated_trials.report_root(tmp_path / 'records', 2, 'fixture-sha')
    for path in (root / 'repeated-trials.json', *sorted((root / 'tasks').glob('*.json'))):
        saved = json.loads(path.read_text())
        assert saved['results'] == report['results']
        assert saved['missing'] == []
        assert saved['metrics'] is None


@pytest.mark.parametrize(('outcomes', 'diagnostic_index'), [
    (['unmeasured', 'measured', 'measured'], 0),
    (['provider', 'measured', 'measured'], 0),
    (['invalid', 'unmeasured', 'measured'], 1),
    (['unmeasured', 'measured', 'invalid'], 2),
])
def test_failed_progress_uses_the_same_trial_as_its_reason(tmp_path, monkeypatch, outcomes, diagnostic_index):
    result, calls, attempts, _ = run_trials(tmp_path, monkeypatch, outcomes)
    assert len(calls) == 3
    failed = attempts[diagnostic_index]
    directory = tmp_path / 'records/progress/round-2'
    record = json.loads((directory / 't.json').read_text())
    assert record['invalid'] == result['invalid']['t']
    assert f'seed {calls[diagnostic_index]["run_seed"]}' in record['invalid']['detail']
    assert record['harness_output']['exit_code'] == failed['exit_code']
    assert record['harness_output']['stderr'] == failed['stderr']
    assert record['harness_output']['runtime_services'] == failed['runtime_services']
    assert result['harness_runtime'] == [failed['harness_runtime']]
    assert result['runtime_diagnostics']['t'] == failed['runtime_services']
    assert (directory / 'traces/t.invalid.jsonl').read_text() == failed['trace']


@pytest.mark.parametrize('failure', ['invalid', 'unmeasured', 'provider'])
def test_all_failed_declared_trials_are_recorded_without_zero_scores(tmp_path, monkeypatch, failure):
    result, calls, _, cache = run_trials(tmp_path, monkeypatch, [failure] * 3)
    assert len(calls) == 3 and cache == {}
    report = result['repeated_trials']
    assert report['missing'] == [] and len(report['invalid']) == 3
    assert [row['score'] for row in report['results']] == [None] * 3
    assert not report['complete'] and report['metrics'] is None
    assert result['per_task'] == {} and 't' in result['invalid']


@pytest.mark.parametrize('failure', ['invalid', 'unmeasured', 'provider'])
def test_undeclared_trials_keep_existing_early_failure_behavior(tmp_path, monkeypatch, failure):
    result, calls, _, cache = run_trials(
        tmp_path, monkeypatch, [failure, 'measured', 'measured'], declared=False)
    assert len(calls) == 1 and cache == {}
    assert result.get('repeated_trials') is None
    assert result['per_task'] == {} and 't' in result['invalid']
