"""Reports retain the platform validity decision instead of trusting raw scorer scores."""
from copy import deepcopy
import json
import shutil
import subprocess
import sys

import pytest

from eval.run import runner
from test_costbench_official_scorer import ASSETS, archive, evidence, reporting_functions
from test_costbench_model_failure import (
    endpoint, selected, test_http_failure_does_not_become_task_score as http_control,
)


def test_raw_verifier_evidence_cannot_establish_platform_validity():
    raw = evidence()
    with pytest.raises(ValueError, match='complete platform trial archive'):
        reporting_functions()([raw], [raw['binding']])


@pytest.mark.parametrize('mutation,reason', [
    (lambda row: row['archive']['result'].update(invalid={'reason': 'configured_http_status'}), 'platform-valid'),
    (lambda row: row['archive']['result'].update(exception={'type': 'OSError'}), 'platform-valid'),
    (lambda row: row['archive']['result']['verdict'].update(unmeasured='scorer failed'), 'platform-valid'),
    (lambda row: row['archive']['outcome'].update(status='unmeasured'), 'platform-valid'),
    (lambda row: row['archive']['outcome'].update(status='raised'), 'platform-valid'),
    (lambda row: row['archive']['binding'].update(seed='different-seed'), 'binding mismatch'),
    (lambda row: row['archive']['result'].update(task_id='different-task'), 'binding mismatch'),
    (lambda row: row['archive']['outcome'].update(trial_id='different-trial'), 'outcome differs'),
    (lambda row: row['archive']['result']['verdict'].update(score=None), 'binary score'),
    (lambda row: row['archive']['result']['verdict'].update(score=0), 'reward disagree'),
    (lambda row: row['progress'].update(invalid={'stage': 'harness'}), 'platform-valid'),
    (lambda row: row['progress'].update(complete=False), 'platform-valid'),
    (lambda row: row['progress'].update(n_trials=0), 'discarded trials'),
    (lambda row: row['progress'].update(trial_scores=[]), 'discarded trials'),
    (lambda row: row['progress'].update(task_id='wrong-task'), 'identity mismatch'),
    (lambda row: row['progress'].update(trial_scores=[0]), 'scores disagree'),
    (lambda row: row['reference'].update(sha256='different-digest'), 'hash or outcome differs'),
])
def test_archive_rejects_unmeasured_trials_and_disagreeing_bindings(mutation, reason):
    row = archive(evidence())
    expected = [deepcopy(row['archive']['binding'])]
    mutation(row)
    with pytest.raises(ValueError, match=reason):
        reporting_functions()([row], expected)


@pytest.mark.parametrize('status', ['returned', 'measured'])
def test_valid_zero_survives_nonzero_actor_exit(status):
    raw = evidence()
    raw['official']['reward'] = 0
    row = archive(raw)
    row['archive']['outcome']['status'] = status
    row['progress']['trial_evidence'][0]['status'] = status
    row['archive']['result']['exit_code'] = 1
    assert len(reporting_functions()([row], [row['archive']['binding']])) == 1


@pytest.mark.parametrize('failure', ['provider', 'signal', 'discarded-provider'])
def test_report_checks_final_progress_after_trial_archive(tmp_path, monkeypatch, failure):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    tid = 'costbench/test/Query00001'
    returned = []

    def actor(_repo, task, **kwargs):
        raw = evidence(tid, kwargs['trial_id'])
        raw['binding'].update(run_id=kwargs['run_id'], seed=kwargs['run_seed'])
        raw['official']['reward'] = 0
        result = {'task_id': tid, 'answer': '', 'trace': '', 'exit_code': 0, 'stderr': '',
                  'verdict': {'score': 0, 'passed': False, 'evidence': raw}}
        affected = failure != 'discarded-provider' or len(returned) == 1
        if affected:
            result.update(exit_code=-9 if failure == 'signal' else 1,
                          stderr='' if failure == 'signal' else 'APIConnectionError: Connection error')
        returned.append(deepcopy(result))
        return result

    # The actual runner makes its final provider/signal decision after archiving
    # these controlled results. No report-specific classifier is substituted.
    monkeypatch.setattr(runner, 'run_one', actor)
    run = tmp_path / 'run'
    measured = runner._evaluate(work, [{'task_id': tid}], {}, sandbox=True,
        verifiers={tid: {'kind': 'answer', 'expected': '', 'trial_evidence': {'version': 1}}},
        trials=3 if failure == 'discarded-provider' else 1,
        run_id='report-final-progress', run_seed='42', harness_sha='fixture', progress_dir=run)
    paths = sorted((run / 'trial-evidence').glob('round-*/*/*/*.json'))
    records = [json.loads(path.read_text()) for path in paths]
    assert all(not row['result'].get('invalid') for row in records)
    progress = json.loads((run / 'progress/round-0' / (tid + '.json')).read_text())
    if failure == 'discarded-provider':
        assert tid not in measured['invalid'] and progress['complete']
        assert progress['n_trials'] == 2 and len(progress['trial_evidence']) == 3
        reason = 'discarded trials cannot be inferred'
    else:
        assert tid in measured['invalid'] and progress['invalid']
        reason = 'platform-valid'
    expected = tmp_path / 'expected.json'
    expected.write_text(json.dumps([row['binding'] for row in records]))
    command = [sys.executable, '-B', str(ASSETS / 'report.py'), '--bundle', 'unused',
               '--runtime', 'unused', '--expected-cells', str(expected), *map(str, paths)]
    rejected = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert rejected.returncode != 0 and not rejected.stdout
    assert reason in rejected.stderr


@pytest.mark.parametrize('http_status,handling', [(429, 'recovered'), (200, 'healthy'), (200, 'spoofed')])
def test_report_cli_uses_real_platform_validity(selected, tmp_path, monkeypatch, endpoint,
                                               http_status, handling):
    # This existing control exercises the real runner, sandbox, relay and pinned
    # scorer against a local HTTP fixture. Oracle actions are control-only.
    http_control(selected, tmp_path, monkeypatch, endpoint, http_status, handling)
    paths = list((tmp_path / 'run/trial-evidence').glob('round-*/*/*/*.json'))
    assert len(paths) == 1
    saved_bytes = paths[0].read_bytes()
    row = json.loads(saved_bytes)
    expected = tmp_path / 'expected-cells.json'
    expected.write_text(json.dumps([row['binding']]))
    dataset, _ = selected
    command = [sys.executable, '-B', str(ASSETS / 'report.py'),
               '--bundle', str(dataset.DATASET_ROOT / 'bundle'),
               '--runtime', str(dataset.RUNTIME), '--expected-cells', str(expected)]
    if dataset.__name__.endswith('costbench_dynamic'):
        command += ['--mode', 'cost_change']
    result = subprocess.run([*command, str(paths[0])], capture_output=True, text=True, timeout=60)
    if http_status == 429:
        assert row['result']['invalid']['reason'] == 'configured_http_status'
        assert row['result']['verdict']['score'] == 1
        assert result.returncode != 0 and not result.stdout
        assert 'platform-valid' in result.stderr
    else:
        assert not row['result'].get('invalid')
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report['complete'] is True and report['primary_denominator'] == 1
        assert report['primary_mean'] == float(handling == 'healthy')
        assert report['official_raw'] == row['result']['verdict']['evidence']['official']['official']['official_raw']
        if handling == 'spoofed':
            assert row['result']['exit_code'] != 0
    assert paths[0].read_bytes() == saved_bytes
    if handling == 'healthy':
        # Whole run directories can move; a detached archive cannot establish
        # final validity. Changed progress must fail its existing index hash.
        relocated = tmp_path / 'relocated'
        shutil.copytree(tmp_path / 'run', relocated)
        moved_archive = relocated / paths[0].relative_to(tmp_path / 'run')
        moved = subprocess.run([*command, str(moved_archive)], capture_output=True, text=True, timeout=60)
        assert moved.returncode == 0, moved.stderr
        progress_path = relocated / 'progress/round-0' / (row['binding']['task_id'] + '.json')
        progress = json.loads(progress_path.read_text())
        progress['score'] = .5
        progress_path.write_text(json.dumps(progress))
        changed = subprocess.run([*command, str(moved_archive)], capture_output=True, text=True, timeout=60)
        assert changed.returncode != 0 and 'final task progress differs' in changed.stderr
        detached = tmp_path / 'detached.json'
        detached.write_bytes(saved_bytes)
        missing_progress = subprocess.run([*command, str(detached)], capture_output=True, text=True, timeout=60)
        assert missing_progress.returncode != 0 and 'standard run directory layout' in missing_progress.stderr
    # The old extraction workflow must fail even when its scorer score is valid.
    raw_path = tmp_path / 'raw-verifier-evidence.json'
    raw_path.write_text(json.dumps(row['result']['verdict']['evidence']))
    legacy = subprocess.run([*command, str(raw_path)], capture_output=True, text=True, timeout=60)
    assert legacy.returncode != 0 and not legacy.stdout
    assert 'complete platform trial archive required' in legacy.stderr
