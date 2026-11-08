"""Donor history and recipient spend remain separate; no model or endpoint.

Real local trusted dispatch reports feed EventCollector and the ordinary driver,
H0 admission, runner, noop method, summary and conservative export projections.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def control(root, phase):
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests/quality')]
    from test_unknown_harness_usage import parsed, finish, broker_report
    from eval.run import runner
    from harnessgrad.loops import mode_a
    from harnessgrad import config
    from harnessgrad.budget_integration import bind_budget
    import driver
    source = root / 'authored-harness'
    if not source.exists():
        source.mkdir()
        (source / 'harness.json').write_text(json.dumps({'name': 'authored-h0-cost', 'version': '1',
            'entrypoint': 'agent.py', 'backend': 'cli', 'env_kinds': ['files']}))
        (source / 'agent.py').write_text('# Offline actor transport is supplied by this authored control.\n')
    profile = root / 'budget-profile.json'
    if not profile.exists():
        profile.write_text(json.dumps({'profile_version': 1, 'limits': {
            'wall_seconds': 600, 'max_requests': 20, 'max_calls': 20, 'max_retries': 4,
            'max_prompt_tokens': 1000, 'max_output_tokens': 1000, 'global_total_tokens': 2000,
            'max_context': 2000, 'candidates': 10, 'rounds': 3, 'trials': 3, 'jobs': 1},
            'roles': {'agent': {'max_total_tokens': 2000}}}))
    payload = {'choices': [{'message': {'content': 'authored local response'}}]}
    if phase != 'donor_env_unknown':
        payload['usage'] = {'prompt_tokens': 7, 'completion_tokens': 2}
    environment = runner._environment_model_usage(broker_report(payload))
    events = []
    def actor(repo, task, **kwargs):
        binding = bind_budget(profile, root / 'runs' / kwargs['run_id'] / 'budget_ledger.json',
                              run_id=kwargs['run_id'], arm_id='agent')
        response = binding.dispatch(lambda request: lambda payload, max_tokens, timeout: {
            'usage': {'prompt_tokens': 10, 'completion_tokens': 3}, 'value': 'authored'},
            {'purpose': 'offline H0 cost control'}, 'agent', 10, 3)
        tokens = {'input': response['usage']['prompt_tokens']}
        if phase != 'donor_unknown':
            tokens['output'] = response['usage']['completion_tokens']
        events.append({'event': 'actor_execution', 'run_id': kwargs['run_id']})
        return {'task_id': task['task_id'], 'trace': parsed([finish(tokens)]), 'env_usage': environment,
                'exit_code': 0, 'stderr': '', 'verdict': {'kind': 'answer', 'passed': True, 'score': 1.0}}
    evaluate, schedule = driver.evaluate, driver._run_mode_a
    def h0_evaluate(*args, **kwargs):
        events.append({'event': 'h0_evaluate'})
        return evaluate(*args, **kwargs)
    def next_evaluate(*args, **kwargs):
        events.append({'event': 'schedule_evaluate'})
        return evaluate(*args, **kwargs)
    def enter_schedule(*args, **kwargs):
        events.append({'event': 'schedule_enter', 'current_cumulative': dict(args[13]),
            'provider_counts': json.loads((root / 'runs' / phase / 'budget_ledger.json').read_text())['counts']})
        return schedule(*args, **kwargs)
    rounds = 1 if phase == 'recipient_fresh' else 0
    argv = ['driver.py', '--harness-path', str(source), '--dataset', 'demo', '--tasks', 't01',
            '--sampling', 'all', '--rounds', str(rounds), '--trials', '1', '--jobs', '1',
            '--no-sandbox', '--improver', 'none', '--run-id', phase, '--runs-root', str(root / 'runs'),
            '--work-root', str(root / 'work'), '--gpu-budget-dir', str(root / 'slots'),
            '--method-entrypoint', f'{sys.executable} {ROOT / "methods/noop/run.py"}',
            '--budget-profile', str(profile)]
    if not phase.startswith('donor'):
        argv.extend(['--h0-from', 'donor_unknown' if phase == 'recipient_unknown' else
                     'donor_env_unknown' if phase == 'recipient_env_unknown' else 'donor'])
    os.environ.update(HG_AGENT_BACKEND='mock', HG_REVIEWED_ENVIRONMENT_ONLY='1', HG_AGENT_MODEL='authored-no-model')
    config.reset()
    with patch.object(sys, 'argv', argv), patch.object(runner, 'run_one', actor), \
         patch.object(driver, 'evaluate', h0_evaluate), patch.object(mode_a, 'evaluate', next_evaluate), \
         patch.object(driver, '_run_mode_a', enter_schedule):
        code = driver.main()
    (root / (phase + '-events.json')).write_text(json.dumps(events, indent=2))
    return code


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp('shared-h0-current-cost')
    for phase in ('donor', 'recipient_h0', 'recipient_fresh', 'donor_unknown', 'recipient_unknown'):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(root), phase],
            cwd=ROOT, env={**os.environ, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'},
            capture_output=True, text=True, timeout=90)
        (root / (phase + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-1000:]
    return root


def records(root, name):
    path = root / 'runs' / name
    return {'events': json.loads((root / (name + '-events.json')).read_text()),
            'curve': [json.loads(line) for line in (path / 'curve.jsonl').read_text().splitlines()],
            'ledger': json.loads((path / 'budget_ledger.json').read_text()),
            'summary': json.loads((path / 'end-summary.json').read_text()),
            'meta': json.loads((path / 'run_meta.json').read_text())}


def test_shared_h0_keeps_history_but_adds_no_current_measurement(runs):
    donor, recipient = records(runs, 'donor'), records(runs, 'recipient_h0')
    assert not any(event['event'] in ('actor_execution', 'h0_evaluate') for event in recipient['events'])
    assert recipient['ledger']['counts']['requests'] == recipient['ledger']['counts']['calls'] == 0
    point = recipient['curve'][0]
    assert point['cost'] == donor['curve'][0]['cost']
    assert point['cumulative'] == donor['curve'][0]['cumulative']
    assert point['shared_from']['run_id'] == 'donor'
    current = next(event['current_cumulative'] for event in recipient['events'] if event['event'] == 'schedule_enter')
    assert current['evaluation_trials'] == 0
    assert current['env_generation_tokens'] == 0
    assert point['current_run_cost']['harness_tokens'] == point['current_run_cost']['harness_model_calls'] == 0


def test_shared_h0_summary_counts_current_spend_and_exposes_reference(runs):
    recipient = records(runs, 'recipient_h0')
    summary = recipient['summary']
    assert summary['calls'] == summary['tokens'] == 0
    reference = summary['cost_provenance']['historical_references'][0]
    assert reference['cost']['harness_tokens'] == 13
    assert reference['cost']['env_generation_tokens'] == 9
    assert summary['cost_provenance']['scope'] == 'current_run_incremental'


def test_fresh_round_after_shared_h0_counts_only_new_work(runs):
    recipient = records(runs, 'recipient_fresh')
    assert sum(event['event'] == 'actor_execution' for event in recipient['events']) == 1
    assert recipient['ledger']['counts']['calls'] == 1 and recipient['ledger']['spent']['total_tokens'] == 13
    assert recipient['curve'][1]['cumulative']['evaluation_trials'] == 1
    assert recipient['curve'][1]['cumulative']['env_generation_tokens'] == 9
    assert recipient['summary']['calls'] == 1 and recipient['summary']['tokens'] == 13


def test_unknown_donor_cost_is_not_changed_to_zero(runs):
    donor, recipient = records(runs, 'donor_unknown'), records(runs, 'recipient_unknown')
    assert donor['curve'][0]['cost']['harness_tokens'] is None
    assert recipient['curve'][0]['cost'] == donor['curve'][0]['cost']
    assert recipient['curve'][0]['cumulative'] == donor['curve'][0]['cumulative']
    assert recipient['ledger']['counts']['calls'] == 0
    assert recipient['curve'][0]['current_run_cost']['harness_tokens'] == 0
    assert recipient['summary']['tokens'] == 0
    assert recipient['summary']['cost_provenance']['historical_references'][0]['cost']['harness_tokens'] is None


def test_export_remains_conservative_for_positive_shared_reference(runs):
    from harnessgrad.evidence_export import _budget
    recipient = records(runs, 'recipient_h0')
    projected = _budget({'points': {point['round']: point for point in recipient['curve']}, 'meta': recipient['meta']})
    assert projected['totals']['harness_tokens']['recorded_sum'] is None
    assert projected['totals']['harness_tokens']['reason'] == 'reused_cost'


def test_legacy_shared_cost_has_no_invented_current_zero():
    from tools.run_summary import cost_totals
    point = {'round': 0, 'shared_from': {'run_id': 'legacy'},
             'cost': {'harness_model_calls': 0, 'harness_tokens': 0}, 'cumulative': {'generation_tokens': 0}}
    notes = []
    result = cost_totals([point], 'train', notes)
    assert result['calls'] is None and result['tokens'] is None
    assert result['cost_provenance']['historical_references'][0]['cost'] == point['cost']
    assert any('legacy' in note and 'unknown' in note for note in notes)


def test_fresh_unknown_environment_stays_unknown_and_shared_delta_is_zero(runs):
    for phase in ('donor_env_unknown', 'recipient_env_unknown'):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(runs), phase],
            cwd=ROOT, env={**os.environ, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'},
            capture_output=True, text=True, timeout=90)
        (runs / (phase + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-1000:]
    donor, recipient = records(runs, 'donor_env_unknown'), records(runs, 'recipient_env_unknown')
    assert donor['curve'][0]['cost']['env_generation_tokens'] is None
    assert donor['curve'][0]['cumulative']['env_generation_tokens'] is None
    assert recipient['curve'][0]['cost'] == donor['curve'][0]['cost']
    assert recipient['curve'][0]['cumulative'] == donor['curve'][0]['cumulative']
    current = next(event['current_cumulative'] for event in recipient['events'] if event['event'] == 'schedule_enter')
    assert current['env_generation_tokens'] == 0
    assert recipient['curve'][0]['current_run_cost']['env_generation_tokens'] == 0


if __name__ == '__main__':
    raise SystemExit(control(Path(sys.argv[1]), sys.argv[2]))
