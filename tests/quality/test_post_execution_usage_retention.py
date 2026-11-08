"""Reported spend survives post-execution declaration refusal.

The actor is authored input; EventCollector, HTTP framing, Judge.report,
evaluation/progress and cost readers are real. No model, endpoint or benchmark.
These regressions intentionally fail on the original 94666e5 branch.
"""
import json

import pytest

from eval.observe.tracing import _last_usage
from eval.run import runner
from eval.service.http_observation import HTTPObservation
from harnessgrad.records import _cost_of
from test_unknown_harness_usage import broker_report, finish, parsed


@pytest.fixture
def refused_after_execution(tmp_path, monkeypatch):
    repo = tmp_path / 'actor'
    repo.mkdir()
    declaration = tmp_path / 'authored-check.py'
    declaration.write_text('VERSION = "before actor"\n')
    trace = parsed([finish({'input': 10, 'output': 3})])
    usage = _last_usage(trace)
    environment = runner._environment_model_usage(broker_report(http_failure=True))
    observation = HTTPObservation()
    connection = observation.connection()
    connection.feed('client', b'POST /v1/chat/completions HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}')
    connection.feed('upstream', b'HTTP/1.1 500 Authored\r\nContent-Length: 2\r\n\r\n{}')
    http_report = observation.report()
    runtime_services = {'container': {'agent_model_http': http_report}}
    calls, cache = [], {}
    def actor(_repo, task, **kwargs):
        calls.append(task['task_id'])
        # Actual declared bytes change after the actor's reported spend.
        declaration.write_text('VERSION = "after actor"\n')
        return {'task_id': task['task_id'], 'trace': trace, 'exit_code': 0, 'stderr': '',
                'env_usage': environment, 'runtime_services': runtime_services,
                'verdict': {'kind': 'command', 'passed': True, 'score': 1.0,
                            'scorer_isolation_class': 'actor-workdir'}}
    monkeypatch.setattr(runner, 'run_one', actor)
    verifier = {'author': {'kind': 'command', 'argv': ['python3', 'authored-check.py'],
                           'inputs': [{'path': 'authored-check.py', 'from': str(declaration)}]}}
    result = runner._evaluate(repo, [{'task_id': 'author', 'goal': 'author-owned control'}], {},
                              verifiers=verifier, cache=cache, sandbox=False,
                              progress_dir=tmp_path / 'run')
    # Scoring/custody must remain refused; retaining spend cannot admit this score.
    assert calls == ['author']
    assert result['invalid']['author']['stage'] == 'verification context'
    assert 'changed during task execution' in result['invalid']['author']['detail']
    assert result['per_task'] == {} and cache == {} and result['cached_tasks'] == []
    progress = json.loads((tmp_path / 'run/progress/round-0/author.json').read_text())
    assert progress['invalid']['stage'] == 'verification context'
    return result, progress, usage, environment, runtime_services


def test_post_execution_refusal_preserves_actor_reported_spend(refused_after_execution):
    result, _, usage, _, _ = refused_after_execution
    assert result['harness_usage_by_task']['author'] == [usage]
    cost = _cost_of(result, 1, 0)
    assert cost['harness_tokens_lower_bound'] == 13
    assert cost['harness_model_calls_lower_bound'] == 1


def test_post_execution_refusal_preserves_progress_usage_records(refused_after_execution):
    _, progress, usage, environment, _ = refused_after_execution
    assert progress['trial_usage'] == [usage]
    assert progress['trial_env_usage'] == [environment]


def test_post_execution_refusal_preserves_environment_attempts(refused_after_execution):
    result, _, _, environment, _ = refused_after_execution
    assert result['environment_usage_by_task']['author'] == [environment]
    cost = _cost_of(result, 1, 0)
    assert cost['env_model_calls'] == cost['env_model_attempts'] == 1
    assert cost['env_generation_tokens'] is None
    assert cost['env_generation_tokens_lower_bound'] == 0
    assert result['env_usage']['unknown_usage_attempts'] == 1


def test_post_execution_refusal_retains_original_http_failure_diagnostic(refused_after_execution):
    result, progress, _, _, runtime_services = refused_after_execution
    assert result['runtime_diagnostics']['author'] == runtime_services
    assert progress['harness_output']['runtime_services'] == runtime_services
    assert runtime_services['container']['agent_model_http']['calls'] == [
        {'operation': 'chat.completions', 'status': 500, 'complete': True}]
