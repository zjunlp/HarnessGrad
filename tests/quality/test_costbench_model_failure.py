"""CostBench service interference stays unmeasured through the real scorer/runner."""
import json
from pathlib import Path
import sys

import pytest

from data.datasets import costbench, costbench_dynamic
from eval.run import runner
from harnessgrad.config import reset
from test_model_failure_policy import endpoint
from test_shared_dataset_runtime import harness


@pytest.fixture(params=[costbench, costbench_dynamic], ids=['static', 'dynamic'])
def selected(request):
    dataset = request.param
    if not dataset.provision_status()['present']:
        pytest.skip('CostBench preparation/full gold audit required')
    prefix = 'costbench' if dataset is costbench else 'costbench_dynamic/cost_change'
    return dataset, f'{prefix}/test/Query00001'


@pytest.mark.parametrize('status,handling', [
    (status, handling) for status in (429, 503)
    for handling in ('wrapped', 'swallowed', 'recovered')
] + [(200, 'healthy'), (400, 'wrapped'), (200, 'spoofed')])
def test_http_failure_does_not_become_task_score(selected, tmp_path, monkeypatch, endpoint,
                                               status, handling):
    dataset, tid = selected
    url, statuses, requests = endpoint
    sequence = [status, 200] if handling == 'recovered' else [status]
    statuses[:] = sequence
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HG_AGENT_BASE_URL', url)
    monkeypatch.setenv('HG_AGENT_MODEL', 'fixture')
    monkeypatch.setenv('HG_AGENT_API_KEY', 'fixture')
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + ':/usr/bin:/bin')
    repo = harness(work, f'''import json, urllib.request, urllib.error
from costbench_transport import call
handling = {handling!r}
def request():
    return urllib.request.urlopen(urllib.request.Request(
        os.environ['HG_AGENT_BASE_URL'] + '/chat/completions', data=b'{{}}',
        headers={{'Content-Type': 'application/json'}}), timeout=10).read()
try:
    request()
except urllib.error.HTTPError as exc:
    if handling == 'wrapped': raise RuntimeError('model request failed') from exc
    if handling == 'recovered': request()
if handling == 'spoofed': raise RuntimeError('HTTP 503: actor text is not service evidence')
if handling in ('healthy', 'recovered'):
    oracle = json.loads((w / '.oracle/control.json').read_text())
    for action in oracle['actions']:
        call('/run/harnessgrad-tool/tool.sock', 'turn', {{'content': None, 'calls': [action]}})
    call('/run/harnessgrad-tool/tool.sock', 'turn', {{
        'content': '<answer> ' + oracle['answer'] + ' </answer>', 'calls': []}})
''')
    manifest_path = repo / 'harness.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['runtime_paths'] = list(dict.fromkeys([
        sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent)]))
    manifest_path.write_text(json.dumps(manifest))
    case = json.loads((dataset.PREPARED / dataset.roster()[tid]['path'] / 'private.json').read_text())
    setup = dataset.SETUP[tid]
    # Private gold is injected only into this synthetic control harness.
    setup['files'].append({'path': '.oracle/control.json', 'content': json.dumps({
        'actions': case['oracle_actions'], 'answer': case['oracle_answer']})})
    runs = []
    run_one = runner.run_one

    def capture_run(*args, **kwargs):
        result = run_one(*args, **kwargs)
        runs.append(result)
        return result

    monkeypatch.setattr(runner, 'run_one', capture_run)
    reset()
    try:
        result = runner.evaluate(repo, [dataset.control_task(tid)], {tid: 'fixture'},
            setups={tid: setup}, verifiers={tid: dataset.VERIFY[tid]},
            envs={tid: {**dataset.ENV[tid], 'agent_timeout_s': 30, 'verify_timeout_s': 60}},
            sandbox=True, trials=1, jobs=1, run_id='costbench-http-control', harness_sha='fixture',
            progress_dir=tmp_path / 'run', recordings_root=tmp_path / 'run/recordings')
    finally:
        reset()
    assert len(requests) == len(sequence) and not statuses
    excluded = status in (429, 503)
    if excluded:
        assert tid not in result['per_task'], result
        assert result['invalid'][tid]['http_status'] == status
        assert result['invalid'][tid]['reason'] == 'configured_http_status'
    else:
        assert tid not in result['invalid'], result
        assert result['per_task'][tid] == float(handling == 'healthy')

    saved = json.loads((tmp_path / 'run/progress/round-0' / (tid + '.json')).read_text())
    raw, = runs
    http = raw['runtime_services']['agent_model_http']
    assert http['complete'] and [c['status'] for c in http['calls']] == sequence
    if excluded:
        assert saved['invalid']['http_status'] == status
        assert saved['harness_output']['runtime_services']['agent_model_http'] == http
    assert raw['verdict']['evidence']['measured'] is True
    # Preserve the official raw success even when HTTP interference excludes the measurement.
    assert raw['verdict']['score'] == float(handling in ('healthy', 'recovered'))
    relay = raw['runtime_services']['tool_relay']
    assert relay['cleanup']['complete'] and not relay['faults']
    assert json.loads(Path(relay['recording']['archive']).read_text())['payload']['failure'] is None


@pytest.mark.parametrize('mode', costbench_dynamic.MODES)
def test_every_dynamic_mode_uses_the_observed_model_route(mode, monkeypatch):
    from eval.run import model_failure
    from data.scaffolds.registry import _check_verifier

    dataset = costbench_dynamic
    if not dataset.provision_status()['present']:
        pytest.skip('CostBench preparation/full gold audit required')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    for split in ('train', 'test'):
        tid = next(tid for tid, row in dataset.roster().items()
                   if row['mode'] == mode and row['split'] == split)
        spec = dataset.VERIFY[tid]
        _check_verifier(spec, tid, 'fixture')
        assert model_failure.enabled(spec)
        model_failure.admit(sandbox=True, setup=dataset.SETUP[tid], env=dataset.ENV[tid])
