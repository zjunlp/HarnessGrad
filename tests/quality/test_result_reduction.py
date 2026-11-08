"""Authored admitted outcome matrices exercise the public reducer/usage wire contract."""
from copy import deepcopy
import json

from eval.run import runner
from harnessgrad.records import _cost_of


def trace(tokens, output=0):
    return json.dumps({'usage': {'input_tokens': tokens, 'output_tokens': output, 'calls': 1}})


def reduced(tmp_path, monkeypatch, outcomes, *, trials=2):
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"authored","entrypoint":"agent.py"}')
    tasks = [{'task_id': tid, 'goal': 'authored reduction'} for tid in outcomes]
    # These are already admitted outcomes; actor/admission controls live in their
    # own suites. This port isolates ordered reduction from execution concurrency.
    monkeypatch.setattr(runner, '_in_parallel', lambda *a, **kw: outcomes)
    monkeypatch.setattr(runner, 'snapshot', lambda *a: {})
    cache = {}
    report = runner._evaluate(repo, tasks, {}, cache=cache, harness_sha='owned', trials=trials,
                              persist_trial_report=False)
    return report, cache


def test_mixed_outcome_contract_and_only_measured_cache_publication(tmp_path, monkeypatch):
    runtime = {'authored': {'identity': 'same'}}
    first = {'trace': trace(3), 'exit_code': 0, 'stderr': '',
             'verdict': {'score': 0.25, 'tests': {'passed': 1, 'failed': 3},
                         'scorer_isolation_class': 'in-process'}}
    last = {'trace': trace(0), 'exit_code': 0, 'stderr': 'A' * 9001,
            'verdict': {'score': 0.75, 'tests': {'passed': 3, 'failed': 1},
                        'scorer_isolation_class': 'in-process'},
            'harness_runtime': runtime, 'runtime_services': {'authored': {'identity': 'same'}},
            'model_gateway': {'published': True, 'target': 'authored:1'}, 'identity': {'harness': 'authored'}}
    cached = {'score': 0.75, 'trace': trace(6, 2), 'per_task_trials': [0.5, 1.0], 'per_task_std': 0.25,
              'trial_traces': [trace(4, 1), trace(6, 2)],
              'trial_verdicts': [{'score': 0.5, 'scorer_isolation_class': 'in-process'},
                                 {'score': 1.0, 'scorer_isolation_class': 'in-process'}],
              'verdict': {'score': 1.0, 'tests': {'passed': 2, 'failed': 0},
                          'scorer_isolation_class': 'in-process'}, 'harness_runtime': runtime}
    cached_before = deepcopy(cached)
    outcomes = {
        'fraction': {'measured': [first, last], 'usage_records': [
            {'usage': {'input_tokens': 3, 'output_tokens': 0, 'calls': 1},
             'env_usage': {'input_tokens': 0, 'output_tokens': 0, 'calls': 0, 'services_reported': 1}},
            {'usage': None, 'env_usage': {'input_tokens': None, 'input_tokens_reported': 5,
                'output_tokens': 2, 'calls': 1, 'services_reported': 1, 'usage_complete': False}}]},
        'cached': {'cached': cached},
        'invalid': {'invalid': {'stage': 'check', 'detail': 'authored unavailable'},
            'harness_runtime': {'authored': {'identity': 'failed'}},
            'runtime_services': {'authored': {'error': 'retained'}}, 'usage_records': [
                {'usage': {'input_tokens': 7, 'output_tokens': 0, 'calls': 1},
                 'env_usage': {'coverage_unknown': True}}]},
        'stopped': {'not_run': True},
        'timeout': {'invalid': {'outcome': 'timeout', 'detail': 'authored deadline'}, 'usage_records': []},
    }
    report, cache = reduced(tmp_path, monkeypatch, outcomes)
    assert report['per_task'] == {'fraction': 0.5, 'cached': 0.75, 'timeout': 0.0}
    assert report['per_task_std'] == {'fraction': 0.25, 'cached': 0.25, 'timeout': 0.0}
    assert report['score_std'] == 0.25
    assert report['per_task_trials'] == {'fraction': [0.25, 0.75], 'cached': [0.5, 1.0]}
    assert report['invalid'] == {'invalid': outcomes['invalid']['invalid']}
    assert report['timeouts'] == {'timeout': outcomes['timeout']['invalid']}
    assert report['not_run'] == ['stopped'] and report['cached_tasks'] == ['cached']
    assert report['per_task_test_rate'] == {'fraction': 0.5}
    assert report['tests_passed'] == 4 and report['tests_total'] == 8
    assert report['traces']['fraction'] == last['trace']
    assert len(report['harness_output']['fraction']['stderr']) == 8005
    assert report['harness_runtime'] == [runtime, outcomes['invalid']['harness_runtime']]
    assert report['runtime_diagnostics']['invalid'] == outcomes['invalid']['runtime_services']
    assert report['model_gateway'] == last['model_gateway']
    assert report['identity_variants'] == [last['identity']]
    assert cached == cached_before, 'cached evidence must not be mutated during projection'
    assert report['tokens']['input'] is None and report['tokens']['input_reported'] == 20
    assert report['tokens']['input_tasks_missing'] == 4
    assert report['env_usage']['input'] is None and report['env_usage']['input_reported'] == 5
    assert report['env_usage']['coverage_unknown'] is True
    assert len(cache) == 1
    key, entry = next(iter(cache.items()))
    assert key.startswith('owned:fraction:2:verification-context:')
    assert entry['score'] == 0.5 and entry['trial_traces'] == [first['trace'], last['trace']]
    assert entry['verdict'] is report['verdicts']['fraction']
    assert set(entry['verification_context']['tasks']) == {'fraction'}


def test_public_cache_reuse_keeps_score_trials_and_stored_evidence(tmp_path, monkeypatch):
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"authored","entrypoint":"agent.py"}')
    calls = []

    def actor(*a, **kw):
        index = len(calls)
        calls.append(index)
        return {'task_id': 't', 'trace': trace(index + 1), 'exit_code': 0, 'stderr': '',
                'verdict': {'score': index, 'passed': bool(index), 'scorer_isolation_class': 'in-process'}}

    monkeypatch.setattr(runner, 'run_one', actor)
    monkeypatch.setattr(runner, 'snapshot', lambda *a: {})
    cache = {}
    tasks = [{'task_id': 't', 'goal': 'authored cache reuse'}]
    first = runner._evaluate(repo, tasks, {}, cache=cache, harness_sha='owned', trials=2,
                             persist_trial_report=False)
    cache_before = deepcopy(cache)
    second = runner._evaluate(repo, tasks, {}, cache=cache, harness_sha='owned', trials=2,
                              persist_trial_report=False)
    assert calls == [0, 1] and second['cached_tasks'] == ['t']
    for key in ('per_task', 'per_task_std', 'per_task_trials', 'score_std', 'trial_traces', 'trial_verdicts'):
        assert first[key] == second[key], key
    assert cache == cache_before


def test_post_context_refusal_still_exposes_reported_spend_and_diagnostics(tmp_path, monkeypatch):
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"authored","entrypoint":"agent.py"}')
    private = tmp_path / 'private.py'
    private.write_text('VERSION=1\n')
    runtime = {'authored': {'identity': 'executed'}}
    services = {'authored': {'identity': 'executed', 'complete': True}}

    def actor(*a, **kw):
        private.write_text('VERSION=2\n')
        return {'task_id': 't', 'trace': trace(7, 1), 'exit_code': 0, 'stderr': 'authored output',
                'verdict': {'score': 1, 'passed': True}, 'harness_runtime': runtime,
                'runtime_services': services,
                'env_usage': {'input_tokens': 3, 'output_tokens': 2, 'calls': 1,
                              'services_reported': 1, 'usage_complete': True}}

    monkeypatch.setattr(runner, 'run_one', actor)
    monkeypatch.setattr(runner, 'snapshot', lambda *a: {})
    cache = {}
    report = runner._evaluate(repo, [{'task_id': 't', 'goal': 'authored'}], {}, cache=cache,
        verifiers={'t': {'kind': 'command', 'argv': ['python3', 'private.py'],
                         'inputs': [{'path': 'private.py', 'from': str(private)}]}},
        progress_dir=tmp_path / 'run', persist_trial_report=False)
    assert not report['per_task'] and not cache
    assert report['invalid']['t']['stage'] == 'verification context'
    assert report['harness_runtime'] == [runtime]
    assert report['runtime_diagnostics'] == {'t': services}
    cost = _cost_of(report, 1, 0)
    assert cost['harness_tokens'] == 8 and cost['env_generation_tokens'] == 5
    progress = json.loads((tmp_path / 'run/progress/round-0/t.json').read_text())
    assert progress['harness_output']['runtime_services'] == services
    assert progress['trial_usage'][0]['input_tokens'] == 7
