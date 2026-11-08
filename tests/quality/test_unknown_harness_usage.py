"""Real OpenCode event parsing through evaluation aggregation and curve cost.

Only actor execution is replaced by authored trace input. No model or HTTP server.
"""
import json

import pytest

from base_harness.opencode.agent import EventCollector
from eval.observe.tracing import _last_usage
from eval.run import runner
from harnessgrad.records import _cost_of


def finish(tokens=None, *, message='m1', reason='stop'):
    part = {'id': 'finish-' + message, 'messageID': message, 'reason': reason}
    if tokens is not None:
        part['tokens'] = tokens
    return {'type': 'step_finish', 'sessionID': 's1', 'part': part}


def parsed(events):
    rows = []
    collector = EventCollector(rows.append)
    for event in events:
        collector.feed(json.dumps(event))
    rows.append({'usage': collector.usage()})
    return '\n'.join(json.dumps(row) for row in rows)


def evaluate(tmp_path, monkeypatch, traces, *, environment=None, trials=1, progress_dir=None):
    def actor(repo, task, **kwargs):
        return {'task_id': task['task_id'], 'trace': traces[task['task_id']],
                'exit_code': 0, 'stderr': '', 'env_usage': (environment or {}).get(task['task_id'], {}),
                'verdict': {'kind': 'answer', 'passed': True, 'score': 1.0}}
    monkeypatch.setattr(runner, 'run_one', actor)
    tasks = [{'task_id': name} for name in traces]
    result = runner._evaluate(tmp_path, tasks, {}, sandbox=False, trials=trials, progress_dir=progress_dir)
    assert not result['invalid']
    return result, _cost_of(result, len(tasks), 0)


@pytest.mark.parametrize('tokens,total,lower,missing', [
    (None, None, 0, (1, 1)),
    ({'input': 10}, None, 10, (0, 1)),
    ({'output': 3}, None, 3, (1, 0)),
    ({'input': 0, 'output': 0}, 0, 0, (0, 0)),
    ({'input': 10, 'output': 3}, 13, 13, (0, 0)),
])
def test_missing_partial_and_real_zero_are_distinct(tmp_path, monkeypatch, tokens, total, lower, missing):
    trace = parsed([finish(tokens)])
    usage = _last_usage(trace)
    assert usage['calls'] == 1
    result, cost = evaluate(tmp_path, monkeypatch, {'author': trace})
    assert result['tokens']['tasks_reported'] == 1
    assert result['tokens']['input_tasks_missing'] == missing[0]
    assert result['tokens']['output_tasks_missing'] == missing[1]
    assert cost['harness_tokens'] == total
    assert cost['harness_tokens_lower_bound'] == lower
    assert cost['harness_model_calls'] == 1


def test_partial_finish_coverage_keeps_sum_without_claiming_complete_total(tmp_path, monkeypatch):
    trace = parsed([finish({'input': 10, 'output': 3}, reason='tool-calls'), finish(message='m2')])
    usage = _last_usage(trace)
    assert (usage['reported_steps'], usage['completed_steps']) == (1, 2)
    assert (usage['input_tokens'], usage['output_tokens']) == (10, 3)
    result, cost = evaluate(tmp_path, monkeypatch, {'author': trace})
    assert result['tokens']['input'] is None and result['tokens']['output'] is None
    assert result['tokens']['input_reported'] == 10 and result['tokens']['output_reported'] == 3
    assert result['tokens']['input_tasks_missing'] == result['tokens']['output_tasks_missing'] == 1
    assert cost['harness_tokens'] is None and cost['harness_tokens_lower_bound'] == 13
    assert cost['harness_model_calls'] == 2


def test_mixed_tasks_include_absent_usage_without_changing_report_count(tmp_path, monkeypatch):
    known = parsed([finish({'input': 10, 'output': 3})])
    missing = parsed([finish()])
    result, cost = evaluate(tmp_path, monkeypatch, {'known': known, 'missing': missing, 'silent': ''})
    assert result['tokens']['tasks_total'] == 3 and result['tokens']['tasks_reported'] == 2
    assert result['tokens']['input_tasks_known'] == 1 and result['tokens']['input_tasks_missing'] == 2
    assert cost['harness_tokens'] is None and cost['harness_tokens_lower_bound'] == 13
    assert cost['harness_model_calls'] is None and cost['harness_model_calls_lower_bound'] == 2


def test_full_session_export_restores_token_coverage_and_completed_turn_count(tmp_path, monkeypatch):
    rows = []
    collector = EventCollector(rows.append)
    collector.feed(json.dumps({'type': 'text', 'sessionID': 's1', 'part': {'messageID': 'm1', 'text': 'authored'}}))
    collector.reconcile({'info': {'id': 's1', 'outcome': 'succeeded', 'tokens': {
        'input': 10, 'output': 3, 'reasoning': 1, 'cache': {'read': 4, 'write': 2}}},
        'messages': [{'type': 'assistant', 'id': 'm1', 'finish': 'stop', 'tokens': None, 'content': []}]})
    rows.append({'usage': collector.usage()})
    trace = '\n'.join(json.dumps(row) for row in rows)
    usage = _last_usage(trace)
    assert usage['reported_steps'] == 0 and usage['completed_steps'] == usage['calls'] == 1
    _, cost = evaluate(tmp_path, monkeypatch, {'author': trace})
    assert cost['harness_tokens'] == 20 and cost['harness_model_calls'] == 1


def test_http_failure_is_a_response_not_a_completed_model_turn(tmp_path, monkeypatch):
    from eval.service.http_observation import HTTPObservation
    observer = HTTPObservation()
    connection = observer.connection()
    connection.feed('client', b'POST /v1/chat/completions HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}')
    assert observer.report()['calls'] == [] and not observer.report()['complete']
    connection.feed('upstream', b'HTTP/1.1 500 Fixture\r\nContent-Length: 2\r\n\r\n{}')
    assert observer.report()['calls'] == [{'operation': 'chat.completions', 'status': 500, 'complete': True}]
    trace = parsed([{'type': 'step_start', 'sessionID': 's1', 'part': {}},
                    {'type': 'error', 'sessionID': 's1', 'error': {'name': 'APIError', 'statusCode': 500}}])
    assert _last_usage(trace)['calls'] == 0  # Observed completed turns, not HTTP attempts.
    _, cost = evaluate(tmp_path, monkeypatch, {'author': trace})
    assert cost['harness_model_calls'] == 0
    assert cost['harness_tokens'] is None


def test_legacy_records_keep_valid_known_totals_but_not_missing_dimensions():
    assert _cost_of({'tokens': {'input': 0, 'output': 0, 'calls': 0, 'tasks_reported': 1}}, 1, 0)['harness_tokens'] == 0
    assert _cost_of({'tokens': {'input': 10, 'output': None, 'tasks_reported': 1}}, 1, 0)['harness_tokens'] is None


def broker_report(payload=None, *, http_failure=False, failed_retry=False):
    """Exercise the real response parser/report; only its HTTP transport is local input."""
    from eval.service.judge import Judge, JudgeUnavailable
    judge = Judge.__new__(Judge)
    judge.spec = {'model': 'authored', 'parameters': {}}
    judge.api_options = judge.model_mismatch = judge.relay = None
    judge.served_model_mismatches = []
    judge.url = 'http://synthetic.invalid/v1/chat/completions'
    judge.role = 'environment'
    judge.calls, judge.attempts, judge.faults = [], [], []
    def transport(data, record):
        if failed_retry:
            judge.attempts.append({'status': 'error', 'usage_known': False, 'usage': None})
        attempt = {'status': 'error' if http_failure else 'ok', 'usage_known': False, 'usage': None}
        judge.attempts.append(attempt)
        if http_failure:
            raise JudgeUnavailable('authored HTTP 503 failure')
        return json.dumps(payload).encode(), attempt
    judge._request = transport
    try:
        judge._call('complete', {'messages': [{'role': 'user', 'content': 'authored control'}]})
    except JudgeUnavailable:
        pass
    return judge.report()


@pytest.mark.parametrize('usage,total,lower', [(None, None, 0),
    ({'prompt_tokens': 0, 'completion_tokens': 0}, 0, 0),
    ({'prompt_tokens': 10, 'completion_tokens': 3}, 13, 13)])
def test_real_broker_missing_known_and_zero_usage_reach_environment_cost(tmp_path, monkeypatch, usage, total, lower):
    payload = {'choices': [{'message': {'content': 'authored'}}]}
    if usage is not None:
        payload['usage'] = usage
    report = broker_report(payload)
    environment = runner._environment_model_usage(report)
    result, cost = evaluate(tmp_path, monkeypatch, {'author': parsed([finish({'input': 0, 'output': 0})])},
                            environment={'author': environment})
    assert result['env_usage']['services_reported'] == 1
    assert cost['env_generation_tokens'] == total
    assert cost['env_generation_tokens_lower_bound'] == lower
    assert cost['env_model_calls'] == cost['env_model_attempts'] == 1
    if usage is None:
        assert report['usage_complete'] is False and result['env_usage']['input'] is None
        assert result['env_usage']['unknown_usage_attempts'] == 1


def test_retry_attempts_and_invalid_response_do_not_erase_reported_spend(tmp_path, monkeypatch):
    payload = {'choices': [{'message': {'content': 'authored'}}],
               'usage': {'prompt_tokens': 10, 'completion_tokens': 3}}
    report = broker_report(payload, failed_retry=True)
    assert len(report['calls']) == 1 and len(report['attempts']) == 2
    _, cost = evaluate(tmp_path, monkeypatch, {'author': parsed([finish()])},
                       environment={'author': runner._environment_model_usage(report)})
    assert cost['env_generation_tokens'] is None and cost['env_generation_tokens_lower_bound'] == 13
    assert cost['env_model_calls'] == 1 and cost['env_model_attempts'] == 2
    # A malformed answer can still have real provider-reported usage.
    malformed = broker_report({'choices': [], 'usage': payload['usage']})
    assert malformed['faults'] and malformed['usage_complete'] is True
    _, cost = evaluate(tmp_path, monkeypatch, {'author': parsed([finish()])},
                       environment={'author': runner._environment_model_usage(malformed)})
    assert cost['env_generation_tokens'] == 13


def test_failed_http_broker_attempt_is_unknown_not_a_free_request(tmp_path, monkeypatch):
    report = broker_report(http_failure=True)
    assert len(report['calls']) == len(report['attempts']) == 1 and not report['usage_complete']
    _, cost = evaluate(tmp_path, monkeypatch, {'author': parsed([])},
                       environment={'author': runner._environment_model_usage(report)})
    assert cost['env_generation_tokens'] is None and cost['env_generation_tokens_lower_bound'] == 0
    assert cost['harness_model_calls'] == 0 and cost['env_model_attempts'] == 1


def test_multiple_trials_charge_every_trace_and_preserve_task_report_count(tmp_path, monkeypatch):
    known = parsed([finish({'input': 10, 'output': 3})])
    report = broker_report({'choices': [{'message': {'content': 'authored'}}],
                           'usage': {'prompt_tokens': 10, 'completion_tokens': 3}})
    result, cost = evaluate(tmp_path, monkeypatch, {'author': known}, trials=3,
                            environment={'author': runner._environment_model_usage(report)})
    assert result['tokens']['tasks_reported'] == 1 and result['tokens']['trials_reported'] == 3
    assert cost['harness_tokens'] == cost['env_generation_tokens'] == 39
    assert cost['harness_model_calls'] == 3 and cost['env_model_attempts'] == 3


def test_service_recordings_keep_independent_dimension_coverage(tmp_path, monkeypatch):
    from eval.service.services import RunningServices, USAGE_FILE
    service = RunningServices.__new__(RunningServices)
    service.recordings = {}
    for name, usage in [('known', {'input_tokens': 10, 'output_tokens': 3, 'calls': 1}),
                        ('partial', {'output_tokens': 5, 'calls': 1})]:
        path = tmp_path / name
        path.mkdir()
        (path / USAGE_FILE).write_text(json.dumps({'usage': usage}))
        service.recordings[name] = path
    usage = service.usage()
    assert usage['services_reported'] == 2 and usage['input_tokens'] is None
    assert usage['input_tokens_reported'] == 10 and usage['output_tokens'] == 8
    result, cost = evaluate(tmp_path, monkeypatch, {'author': parsed([])}, environment={'author': usage})
    assert result['env_usage']['input'] is None and result['env_usage']['output'] == 8
    assert cost['env_generation_tokens'] is None and cost['env_generation_tokens_lower_bound'] == 18
    assert cost['env_usage_completeness']['input_services_missing'] == 1
    assert cost['env_model_calls'] == 2 and cost['env_model_attempts'] is None


def test_resume_preserves_trial_unknowns_and_environment_lower_bounds(tmp_path, monkeypatch):
    from harnessgrad.loops.eval_side import _merge_prior, _progress_results
    missing = parsed([finish()])
    report = broker_report(http_failure=True)
    result, fresh = evaluate(tmp_path, monkeypatch, {'author': missing}, trials=2,
                             environment={'author': runner._environment_model_usage(report)}, progress_dir=tmp_path)
    resumed = _merge_prior({}, _progress_results(tmp_path))
    cost = _cost_of(resumed, 1, 0)
    assert cost['harness_tokens'] is fresh['harness_tokens'] is None
    assert cost['env_generation_tokens'] is fresh['env_generation_tokens'] is None
    assert cost['harness_model_calls'] == fresh['harness_model_calls'] == 2
    assert cost['env_model_attempts'] == fresh['env_model_attempts'] == 2
    assert cost['env_generation_tokens_lower_bound'] == fresh['env_generation_tokens_lower_bound'] == 0
    assert resumed['tokens']['trials_reported'] == result['tokens']['trials_reported'] == 2


def test_resume_restores_known_trials_and_legacy_missing_records_stay_unknown(tmp_path, monkeypatch):
    from harnessgrad.loops.eval_side import _merge_prior, _progress_results
    result, _ = evaluate(tmp_path, monkeypatch, {'known': parsed([finish({'input': 10, 'output': 3})])},
                         trials=2, progress_dir=tmp_path)
    resumed = _merge_prior({}, _progress_results(tmp_path))
    assert _cost_of(resumed, 1, 0)['harness_tokens'] == 26
    merged = _merge_prior(result, {'legacy': {'score': 1, 'process_outcome': 'completed'}})
    cost = _cost_of(merged, 2, 0)
    assert cost['harness_tokens'] is None and cost['harness_tokens_lower_bound'] == 26
    assert cost['env_generation_tokens'] is None


def test_summary_does_not_fallback_to_method_zero_for_unknown_harness_cost():
    from tools.run_summary import cost_totals
    point = {'cost': _cost_of({'tokens': {'input': None, 'output': None, 'tasks_reported': 1}}, 1, 0),
             'cumulative': {'generation_tokens': 0}}
    notes = []
    assert cost_totals([point], None, notes)['tokens'] is None
    assert any('incomplete' in note for note in notes)


def test_unmeasured_trial_keeps_known_spend_without_changing_scoring(tmp_path, monkeypatch):
    sequence = iter([{'trace': parsed([finish({'input': 10, 'output': 3})]),
                      'verdict': {'kind': 'answer', 'score': 1, 'passed': True}},
                     {'trace': parsed([finish()]), 'verdict': {'unmeasured': 'authored failed response'}}])
    def actor(*args, **kwargs):
        return {'exit_code': 0, 'stderr': '', **next(sequence)}
    monkeypatch.setattr(runner, 'run_one', actor)
    result = runner._evaluate(tmp_path, [{'task_id': 'author'}], {}, sandbox=False, trials=2)
    assert 'author' in result['invalid'] and not result['per_task']
    cost = _cost_of(result, 1, 0)
    assert cost['harness_tokens'] is None and cost['harness_tokens_lower_bound'] == 13
    assert cost['harness_model_calls'] == 2


def test_missing_service_recording_makes_total_unknown_but_preserves_known_peer(tmp_path, monkeypatch):
    from eval.service.services import RunningServices, USAGE_FILE
    service = RunningServices.__new__(RunningServices)
    known = tmp_path / 'known'
    known.mkdir()
    (known / USAGE_FILE).write_text(json.dumps({'usage': {'input_tokens': 10, 'output_tokens': 3, 'calls': 1}}))
    service.recordings = {'known': known, 'missing': tmp_path / 'missing'}
    usage = service.usage()
    assert usage['services_reported'] == 1 and usage['services_total'] == 2
    _, cost = evaluate(tmp_path, monkeypatch, {'author': parsed([])}, environment={'author': usage})
    assert cost['env_generation_tokens'] is None and cost['env_generation_tokens_lower_bound'] == 13
    assert cost['env_model_calls'] is None


def test_trace_ui_and_legacy_scope_distinguish_unknown_from_measured_zero():
    from eval.observe.tracing import loop_facts
    assert loop_facts(parsed([finish()]))['tokens'] is None
    assert loop_facts(parsed([finish({'input': 0, 'output': 0})]))['tokens'] == 0
    legacy = _cost_of({'tokens': {'input': 0, 'output': 0, 'calls': 0, 'tasks_reported': 1}}, 1, 0)
    assert legacy['harness_tokens'] == 0
    assert legacy['harness_usage_scope'] == 'legacy_reported_coverage_unknown'


def test_summary_marks_legacy_cross_round_partial_tokens_as_lower_bound():
    from tools.run_summary import cost_totals
    notes = []
    totals = cost_totals([{'cost': {'harness_tokens': 13}}, {'cost': {}}], 'eval', notes)
    assert totals['tokens'] == 13
    assert any('harness token counts cover 1 of 2' in note and 'lower bound' in note for note in notes)
