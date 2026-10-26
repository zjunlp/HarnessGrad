"""Tau3 excludes HTTP 429/5xx interference independently of Harness behavior."""
import copy
import json
import shutil

import pytest

from data.datasets import tau3_bench as dataset
from eval.run import model_failure, runner, repeated_trials
from harnessgrad.config import reset
from test_model_failure_policy import endpoint, observed
from test_model_http_invalidation import recovered_success_archive
from test_shared_dataset_runtime import ROOT, harness


@pytest.fixture
def policy():
    if not (dataset.DATASET_ROOT/'manifest.json').exists():
        pytest.skip('prepared tau3 payload required')
    return dataset.VERIFY['tau3_bench/airline/8']['model_failure_policy']


@pytest.mark.parametrize('exit_code,stderr,passed,recovered', [
    (1, 'RuntimeError: wrapped failure', False, False),
    (0, '', False, False),
    (0, '', True, True),
    (1, 'UnrelatedError: final error after task completion', True, True),
])
@pytest.mark.parametrize('complete', [False, True])
def test_service_faults_do_not_depend_on_harness_outcome(policy, exit_code, stderr, passed, recovered, complete):
    for status in (429, *range(500, 600)):
        result = observed(status, stderr=stderr, exit_code=exit_code,
                          verdict={'passed': passed, 'score': int(passed)})
        report = result['runtime_services']['agent_model_http']
        report['complete'] = report['calls'][0]['complete'] = complete
        if recovered:
            report['calls'].append({'operation': 'chat.completions', 'status': 200, 'complete': True})
        original = copy.deepcopy(result)
        invalid = model_failure.failure(result, policy)
        assert invalid is not None, status
        assert invalid['http_status'] == status and invalid['reason'] == 'configured_http_status'
        assert result == original


def test_other_4xx_and_healthy_responses_keep_existing_classification(policy):
    for status in (200, *range(400, 429), *range(430, 500)):
        result = observed(status, stderr='RuntimeError: request failed')
        assert model_failure.failure(result, policy) is None, status


@pytest.mark.parametrize('status', [429, 500])
def test_recovered_service_fault_preserves_raw_success_and_archive(tmp_path, monkeypatch, endpoint, policy, status):
    recovered_success_archive(tmp_path, monkeypatch, endpoint, policy=policy, status=status)


@pytest.mark.skipif(not (dataset.DATASET_ROOT/'manifest.json').exists(), reason='prepared tau3 payload required')
@pytest.mark.parametrize('status,handling,enabled', [
    (503, handling, enabled) for handling in ('sdk', 'wrapped', 'swallowed') for enabled in (False, True)
] + [(status, 'loop', True) for status in (429, 500, 503)])
def test_agent_http_fault_with_healthy_official_environment(tmp_path, monkeypatch, endpoint, status, handling, enabled):
    url, statuses, requests = endpoint
    statuses[:] = [status]
    for role in ('AGENT', 'ENV', 'JUDGE'):
        monkeypatch.setenv(f'HG_{role}_MODEL', 'fixture')
        monkeypatch.setenv(f'HG_{role}_BASE_URL', url)
        monkeypatch.setenv(f'HG_{role}_API_KEY', 'fixture')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HG_AGENT_RETRIES', '1')  # One identical request suffices for this fault control.
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path/'work'))
    monkeypatch.setenv('PATH', str(dataset.RUNTIME/'bin') + ':/usr/bin:/bin')
    reset()
    work = tmp_path/'work'
    work.mkdir()
    if handling == 'loop':
        repo = work/'harness'
        shutil.copytree(ROOT/'base_harness/loop', repo)
        assert (repo/'agent.py').read_bytes() == (ROOT/'base_harness/loop/agent.py').read_bytes()
    else:
        repo = harness(work, f'''from openai import OpenAI
try:
    OpenAI(base_url=os.environ['HG_AGENT_BASE_URL'], api_key=os.environ['HG_AGENT_API_KEY'],
           max_retries=0, timeout=10).chat.completions.create(model='fixture',
           messages=[{{'role':'user','content':'synthetic error test'}}], max_tokens=1)
except Exception as exc:
    if {handling!r} == 'sdk': raise
    if {handling!r} == 'wrapped': raise RuntimeError('custom failure wrapper') from exc
''')
    manifest = json.loads((repo/'harness.json').read_text())
    manifest['runtime_paths'] = list(dataset._python_runtime(dataset.RUNTIME)[1])
    (repo/'harness.json').write_text(json.dumps(manifest))
    tid = 'tau3_bench/airline/8'
    spec = dataset.VERIFY[tid]
    if not enabled:
        spec.pop('model_failure_policy')
    try:
        result = runner.run_one(repo, dataset.control_task(tid), setup=dataset.SETUP[tid], verifier=spec,
            env=dataset.ENV[tid], run_id='fixture', trial_id='fixture-1', run_seed='626729',
            recordings_root=tmp_path/'recordings', timeout_s=30, verify_timeout_s=60)
    finally:
        reset()
    row = repeated_trials.result_row(result, {'task_id': tid, 'trial_id': 'fixture-1', 'seed': '626729'},
                                    provider_failure=runner._provider_failure(result))
    assert len(requests) == 1 and not statuses  # Environment/judge made no model calls.
    assert result['exit_code'] == (0 if handling == 'swallowed' else 1)
    if handling != 'swallowed':
        assert ('RateLimitError' if status == 429 else 'InternalServerError') in result['stderr']
    if handling == 'loop':
        assert result['stderr'].strip().splitlines()[-1].startswith('RuntimeError:')
    if enabled:
        http = result['runtime_services']['agent_model_http']
        assert http['complete'] and [c['status'] for c in http['calls']] == [status]
        assert result['invalid']['http_status'] == status
    relay = result['runtime_services']['tool_relay']
    assert relay['receipt']['failure'] is None and relay['cleanup']['complete']
    assert result['verdict']['evidence']['completion']['scorer_termination_reason'] == 'agent_error'
    assert row['status'] == ('unmeasured' if enabled else 'measured')
    assert row['score'] == (None if enabled else 0)
