"""Configured HTTP interference is independent of Harness errors and recovery."""
import copy
import json

import pytest

from data.scaffolds.registry import _check_verifier
from eval.run import model_failure, runner
from eval.service.http_observation import HTTPObservation
from test_declared_trials_continue import PROTOCOL
from test_model_failure_policy import endpoint, observed
from test_shared_dataset_runtime import harness


POLICY = {'version': 1, 'invalidate_on_http_status': [503]}


@pytest.mark.parametrize('statuses', [None, True, 503, [], [True], ['503'], [399], [600], [503, 503]])
def test_invalid_status_declarations_rejected(statuses):
    with pytest.raises(ValueError, match='model_failure_policy'):
        _check_verifier({'kind': 'answer', 'expected': '', 'model_failure_policy':
            {'version': 1, 'invalidate_on_http_status': statuses}}, 't', 'fixture')


@pytest.mark.parametrize('overrides', [
    {'stderr': 'RuntimeError: wrapped request failure'},
    {'stderr': 'KeyError: unrelated final error'},
    {'stderr': '', 'exit_code': 0},
    {'stderr': '', 'exit_code': 0, 'verdict': {'passed': True, 'score': 1}},
    {'exit_code': 1, 'verdict': {'passed': True, 'score': 1}},
])
@pytest.mark.parametrize('recovered', [False, True])
def test_confirmed_503_invalidates_every_outcome_without_changing_raw_result(overrides, recovered):
    result = observed(**overrides)
    if recovered:
        result['runtime_services']['agent_model_http']['calls'].append(
            {'operation': 'chat.completions', 'status': 200, 'complete': True})
    original = copy.deepcopy(result)
    failure = model_failure.failure(result, POLICY)
    assert failure['http_status'] == 503
    assert failure['reason'] == 'configured_http_status'
    assert failure['operation'] == 'chat.completions' and failure['call_index'] == 0
    assert result == original


@pytest.mark.parametrize('status', [200, 400, 401, 413, 429, 500, 502, 504])
@pytest.mark.parametrize('exit_code', [0, 1])
def test_unlisted_statuses_are_not_invalidated_by_exception_text(status, exit_code):
    result = observed(status, stderr=f'HTTPError: HTTP Error {status}', exit_code=exit_code)
    assert model_failure.failure(result, POLICY) is None


def test_policy_is_opt_in_and_dataset_selects_the_statuses():
    result = observed(503, stderr='', exit_code=0, verdict={'passed': True, 'score': 1})
    assert not model_failure.enabled({})
    with pytest.raises(ValueError, match='invalidate_on_http_status'):
        model_failure.enabled({'model_failure_policy': {'version': 1}})
    result['runtime_services']['agent_model_http']['calls'][0]['status'] = 429
    assert model_failure.failure(result, {'version': 1, 'invalidate_on_http_status': [429]})['http_status'] == 429


@pytest.mark.parametrize('receipt', [
    {},
    {'version': 1, 'complete': False, 'calls': []},
    {'version': 1, 'complete': True, 'calls': [{'operation': 'other', 'status': 503, 'complete': True}]},
])
def test_stderr_without_inference_receipt_is_not_503_evidence(receipt):
    result = observed(stderr='HTTP 503: wrapped failure',
                      runtime_services={'agent_model_http': receipt})
    assert model_failure.failure(result, POLICY) is None


@pytest.mark.parametrize('body_complete', [False, True])
@pytest.mark.parametrize('operation', ['chat/completions', 'responses', 'completions'])
def test_confirmed_status_survives_partial_body_or_another_broken_connection(body_complete, operation):
    obs = HTTPObservation()
    pair = obs.connection()
    pair.feed('client', f'POST /v1/{operation} HTTP/1.1\r\nContent-Length: 0\r\n\r\n'.encode())
    pair.feed('upstream', b'HTTP/1.1 503 Unavailable\r\nContent-Length: 5\r\n\r\n'
              + (b'error' if body_complete else b'er'))
    if body_complete:
        # A different connection cannot erase an already observed 503.
        obs.connection().feed('client', b'not valid HTTP\r\n\r\n')
    report = obs.report()
    assert not report['complete'] and report['calls'][0]['complete'] == body_complete
    result = observed(stderr='', exit_code=0, runtime_services={'agent_model_http': report})
    assert model_failure.failure(result, POLICY)['http_status'] == 503


def test_existing_invalid_reason_and_receipt_are_preserved():
    result = observed(invalid={'stage': 'tool relay', 'detail': 'fixture'})
    original = copy.deepcopy(result)
    assert model_failure.failure(result, POLICY) is None
    assert result == original


@pytest.mark.parametrize('response', [
    b'HTTP/1.1 503 Unavailable\r\n',  # No complete response headers.
    b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n503',
])
def test_partial_headers_and_body_text_are_not_a_confirmed_error_status(response):
    obs = HTTPObservation()
    pair = obs.connection()
    pair.feed('client', b'POST /v1/chat/completions HTTP/1.1\r\nContent-Length: 0\r\n\r\n')
    pair.feed('upstream', response)
    result = observed(runtime_services={'agent_model_http': obs.report()})
    assert model_failure.failure(result, POLICY) is None


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('mode', ['wrapped', 'swallowed', 'recovered', 'clean', 'spoofed', 'other',
                                  'independent', 'pass-before-fault'])
def test_real_route_classifies_independently_of_harness_output(tmp_path, monkeypatch, endpoint, enabled, mode):
    url, statuses, requests = endpoint
    sequence = [503, 200] if mode in ('recovered', 'independent') else [200 if mode in ('clean', 'spoofed') else 503]
    statuses[:] = sequence * 3
    work = tmp_path/'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HG_AGENT_BASE_URL', url)
    repo = harness(work, f'''import urllib.request, urllib.error
mode = {mode!r}
def request():
    path = '/models' if mode == 'other' else '/chat/completions'
    return urllib.request.urlopen(urllib.request.Request(os.environ['HG_AGENT_BASE_URL']+path,
        data=b'{{}}', headers={{'Content-Type':'application/json'}})).read()
if mode == 'pass-before-fault': (w/'answer.txt').write_text('yes')
if mode == 'independent':
    from concurrent.futures import ThreadPoolExecutor, wait
    with ThreadPoolExecutor() as pool:
        failed = pool.submit(request)
        wait([failed])
        pool.submit(request).result()
        failed.result()
else:
    try:
        request()
    except urllib.error.HTTPError as exc:
        if mode == 'wrapped': raise RuntimeError('custom wrapper') from exc
        if mode == 'recovered': request()
        elif mode != 'swallowed': raise
if mode == 'spoofed': raise RuntimeError('HTTP 503: provider unavailable')
if mode in ('clean', 'recovered'): (w/'answer.txt').write_text('yes')
''')
    spec = {'kind': 'answer', 'expected': 'yes', 'trial_protocol': PROTOCOL}
    if enabled:
        spec['model_failure_policy'] = POLICY
    result = runner.evaluate(repo, [{'task_id': 'fixture/t', 'goal': ''}], {}, trials=3, jobs=1,
        envs={'fixture/t': {'kind': 'files', 'network': 'none'}}, verifiers={'fixture/t': spec},
        run_id='fixture', harness_sha='fixture', progress_dir=tmp_path/'run')
    invalid = enabled and mode in ('wrapped', 'swallowed', 'recovered', 'independent', 'pass-before-fault')
    rows = result['repeated_trials']['results']
    assert [r['status'] for r in rows] == ['unmeasured' if invalid else 'measured'] * 3
    assert [r['score'] for r in rows] == [None if invalid else int(mode in ('clean', 'recovered', 'pass-before-fault'))] * 3
    assert result['repeated_trials']['complete'] == (not invalid)
    assert all(('agent_model_http' in (r['runtime_services'] or {})) == enabled for r in rows)
    assert len(requests) == len(sequence)*3 and not statuses
    assert not (tmp_path/'run/trial-evidence').exists()


def recovered_success_archive(tmp_path, monkeypatch, endpoint, *, policy, status):
    url, statuses, requests = endpoint
    statuses[:] = [status, 200, 200, 200]  # First trial recovers; the next two are clean.
    work = tmp_path/'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HG_AGENT_BASE_URL', url)
    repo = harness(work, '''import urllib.request, urllib.error
def request():
    return urllib.request.urlopen(urllib.request.Request(os.environ['HG_AGENT_BASE_URL']+'/chat/completions',
        data=b'{}', headers={'Content-Type':'application/json'})).read()
try: request()
except urllib.error.HTTPError: request()
(w/'answer.txt').write_text('yes')
(w/'trace.jsonl').write_text('{"event":"completed"}\\n')
''')
    check = '''import json
from pathlib import Path
assert Path('answer.txt').read_text() == 'yes'
Path('verifier/reward.txt').write_text('1')
Path('verifier/evidence.json').write_text(json.dumps({'matched': True}))
'''
    spec = {'kind': 'command', 'argv': ['python', 'check.py'],
            'inputs': [{'path': 'check.py', 'content': check}],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'trial_protocol': PROTOCOL, 'model_failure_policy': policy, 'trial_evidence': {'version': 1}}
    result = runner.evaluate(repo, [{'task_id': 'fixture/t', 'goal': ''}], {}, trials=3, jobs=1,
        envs={'fixture/t': {'kind': 'files', 'network': 'none'}}, verifiers={'fixture/t': spec},
        run_id='fixture', harness_sha='fixture', progress_dir=tmp_path/'run')
    report = result['repeated_trials']
    rows = report['results']
    assert [r['status'] for r in rows] == ['unmeasured', 'measured', 'measured']
    assert [r['score'] for r in rows] == [None, 1, 1]
    assert not report['complete'] and report['metrics'] is None and report['per_task'] is None
    assert len(requests) == 4 and not statuses
    archives = [json.loads((tmp_path/'run'/r['trial_evidence']['path']).read_text()) for r in rows]
    for archive in archives:
        raw = archive['result']
        assert raw['exit_code'] == 0 and raw['verdict']['passed'] and raw['verdict']['score'] == 1
        assert raw['verdict']['evidence'] == {'matched': True}
        assert raw['trace'] == '{"event":"completed"}\n'
        assert raw['trial_evidence_output']['trace'] == raw['trace']
    invalid = archives[0]
    assert invalid['outcome']['score'] is None and invalid['result']['invalid']['http_status'] == status
    assert [c['status'] for c in invalid['result']['runtime_services']['agent_model_http']['calls']] == [status, 200]
    assert all('invalid' not in a['result'] for a in archives[1:])


def test_recovered_success_keeps_archive_but_not_a_formal_score(tmp_path, monkeypatch, endpoint):
    recovered_success_archive(tmp_path, monkeypatch, endpoint, policy=POLICY, status=503)
