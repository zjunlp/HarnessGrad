"""Official tau3 scoring cannot turn a terminal agent HTTP outage into a valid zero."""
import json

import pytest

from data.datasets import tau3_bench as dataset
from eval.run import runner, repeated_trials
from harnessgrad.config import reset
from test_model_failure_policy import endpoint
from test_shared_dataset_runtime import harness


@pytest.mark.skipif(not (dataset.DATASET_ROOT/'manifest.json').exists(), reason='prepared tau3 payload required')
@pytest.mark.parametrize('enabled', [False, True])
def test_agent_503_with_healthy_official_environment(tmp_path, monkeypatch, endpoint, enabled):
    url, statuses, requests = endpoint
    statuses[:] = [503]
    for role in ('AGENT', 'ENV', 'JUDGE'):
        monkeypatch.setenv(f'HG_{role}_MODEL', 'fixture')
        monkeypatch.setenv(f'HG_{role}_BASE_URL', url)
        monkeypatch.setenv(f'HG_{role}_API_KEY', 'fixture')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path/'work'))
    monkeypatch.setenv('PATH', str(dataset.RUNTIME/'bin') + ':/usr/bin:/bin')
    reset()
    work = tmp_path/'work'
    work.mkdir()
    repo = harness(work, '''from openai import OpenAI
OpenAI(base_url=os.environ['HG_AGENT_BASE_URL'], api_key=os.environ['HG_AGENT_API_KEY'],
       max_retries=0, timeout=10).chat.completions.create(model='fixture',
       messages=[{'role':'user','content':'synthetic error test'}], max_tokens=1)
''')
    (repo/'harness.json').write_text(json.dumps({'entrypoint': 'agent.py',
        'runtime_paths': list(dataset._python_runtime(dataset.RUNTIME)[1])}))
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
    assert result['exit_code'] == 1 and 'InternalServerError' in result['stderr']
    relay = result['runtime_services']['tool_relay']
    assert relay['receipt']['failure'] is None and relay['cleanup']['complete']
    assert result['verdict']['evidence']['completion']['scorer_termination_reason'] == 'agent_error'
    assert row['status'] == ('unmeasured' if enabled else 'measured')
    assert row['score'] == (None if enabled else 0)
