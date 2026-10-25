"""Opt-in archives survive incomplete groups without changing evaluation semantics."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from data.scaffolds.registry import _check_verifier
from eval.run import repeated_trials, runner, trial_evidence
from test_declared_trials_continue import PROTOCOL


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    attempts = []

    def execute(outcomes, *, enabled=True, directory='run', protocol=True, **kwargs):
        calls = []

        def actor(_repo, task, **kw):
            i = len(calls)
            calls.append(kw)
            if outcomes[i] == 'interrupt':
                raise KeyboardInterrupt('fixture interruption')
            result = {'task_id': task['task_id'], 'answer': 'answer', 'exit_code': 0,
                      'stderr': f'stderr {i}', 'trace': f'private trace {i}',
                      'verdict': {'score': int(i != 1), 'passed': i != 1,
                                  'evidence': {'details': f'private grading {i}'}},
                      'runtime_services': {'tool_relay': {'recording': {'archive': f'episode-{i}.json'}}}}
            if outcomes[i] == 'success':
                result['verdict'].update(score=1, passed=True)
            if outcomes[i] == 'invalid':
                result['invalid'] = {'stage': 'tool relay', 'detail': f'fault {i}'}
            elif outcomes[i] == 'unmeasured':
                result['verdict'].update(unmeasured='scorer failed', score=None)
            elif outcomes[i] == 'provider':
                result.update(exit_code=1, stderr='APIConnectionError: Connection error',
                              verdict={'score': 0, 'passed': False})
            attempts.append(copy.deepcopy(result))
            return result

        monkeypatch.setattr(runner, 'run_one', actor)
        spec = {'kind': 'answer', 'expected': 'answer'}
        if protocol:
            spec['trial_protocol'] = PROTOCOL
        if enabled:
            spec['trial_evidence'] = {'version': 1}
        cache = {}
        args = dict(trials=3, run_id='fixture', harness_sha='harness', round_no=2,
                    progress_dir=tmp_path / directory, verifiers={'ordinary/task': spec}, cache=cache)
        args.update(kwargs)
        result = runner._evaluate(work, [{'task_id': 'ordinary/task'}], {}, **args)
        return result, calls, cache

    return execute, attempts


@pytest.mark.parametrize('failure', ['invalid', 'unmeasured', 'provider'])
@pytest.mark.parametrize('position', [0, 1, 2])
def test_each_returned_trial_survives_an_incomplete_group(evaluation, tmp_path, failure, position):
    execute, attempts = evaluation
    outcomes = ['measured'] * 3
    outcomes[position] = failure
    result, calls, cache = execute(outcomes)
    report = result['repeated_trials']
    assert len(calls) == 3 and not cache
    assert not report['complete'] and report['metrics'] is None
    assert report['per_task'] is None
    for i, row in enumerate(report['results']):
        reference = row['trial_evidence']
        path = tmp_path / 'run' / reference['path']
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == reference['sha256']
        record = json.loads(content)
        assert record['binding'] == dict(run_id='fixture', round=2, harness_sha='harness',
            task_id='ordinary/task', trial_id=calls[i]['trial_id'], seed=calls[i]['run_seed'])
        assert record['result'] == attempts[i]
        assert row['status'] == ('unmeasured' if i == position else 'measured')
        assert record['outcome'] == {k: v for k, v in row.items() if k != 'trial_evidence'}


@pytest.mark.parametrize('outcomes', [
    ['measured'] * 3, ['invalid', 'success', 'invalid'], ['invalid'] * 3,
    ['provider', 'measured', 'measured'], ['measured', 'provider', 'measured'],
])
def test_switch_changes_only_archive_references_not_scoring_or_feedback(evaluation, tmp_path, outcomes):
    execute, _ = evaluation
    enabled, enabled_calls, enabled_cache = execute(outcomes)
    disabled, disabled_calls, disabled_cache = execute(outcomes, enabled=False, directory='legacy')
    enabled_calls = copy.deepcopy(enabled_calls)
    for call in enabled_calls:
        call['verifier'].pop('trial_evidence', None)
    assert enabled_calls == disabled_calls and enabled_cache == disabled_cache == {}
    for row in enabled['repeated_trials']['results']:
        row.pop('trial_evidence')
    assert enabled == disabled
    assert not (tmp_path / 'legacy/trial-evidence').exists()
    # An additional archive never appears in the legacy progress/feedback files.
    for path in (tmp_path / 'run/progress').rglob('*'):
        if path.is_file():
            assert path.read_bytes() == (tmp_path / 'legacy' / path.relative_to(tmp_path / 'run')).read_bytes()


def test_completed_trial_is_persisted_before_later_interruption(evaluation, tmp_path):
    execute, attempts = evaluation
    with pytest.raises(KeyboardInterrupt):
        execute(['measured', 'interrupt', 'measured'])
    files = list((tmp_path / 'run/trial-evidence').rglob('*.json'))
    assert len(files) == 1 and json.loads(files[0].read_text())['result'] == attempts[0]
    root = repeated_trials.report_root(tmp_path / 'run', 2, 'harness')
    assert list((root / 'in-flight').glob('*.json'))
    assert not (root / 'repeated-trials.json').exists()
    plan = repeated_trials.plan([{'task_id': 'ordinary/task'}], PROTOCOL,
                                run_id='fixture', round_no=2, harness_sha='harness')
    with pytest.raises(ValueError, match='in-flight'):
        repeated_trials.resume_rows(root, plan, {})


def test_archive_write_failure_stops_publication_and_keeps_earlier_trials(evaluation, tmp_path, monkeypatch):
    execute, attempts = evaluation
    original = trial_evidence.immutable_json

    def write(path, record, **kwargs):
        if record['binding']['seed'] == '20':
            raise OSError('fixture disk full')
        original(path, record, **kwargs)

    monkeypatch.setattr(trial_evidence, 'immutable_json', write)
    with pytest.raises(OSError, match='disk full'):
        execute(['measured'] * 3)
    assert len(attempts) == 2
    assert len(list((tmp_path / 'run/trial-evidence').rglob('*.json'))) == 1
    assert not list((tmp_path / 'run').rglob('repeated-trials.json'))


@pytest.mark.parametrize('declaration', [None, False, True, {}, {'version': True},
                                      {'version': 2}, {'version': 1, 'extra': 0}])
def test_invalid_optin_is_rejected(declaration):
    with pytest.raises(ValueError, match='trial_evidence'):
        _check_verifier({'kind': 'answer', 'expected': '', 'trial_protocol': PROTOCOL,
                         'trial_evidence': declaration}, 't', 'fixture')


@pytest.mark.parametrize('overrides', [
    {'protocol': False}, {'progress_dir': None}, {'sandbox': False},
    {'envs': {'ordinary/task': {'kind': 'exec'}}},
    {'setups': {'ordinary/task': {'workers': []}}},
    {'directory': 'work/exposed-records'},
])
def test_unsupported_optin_fails_before_running(evaluation, overrides):
    execute, attempts = evaluation
    with pytest.raises(ValueError, match='trial_evidence'):
        execute(['measured'] * 3, **overrides)
    assert not attempts


def test_legacy_nonprotocol_evaluation_keeps_early_stop_and_no_archive(evaluation, tmp_path, monkeypatch):
    execute, _ = evaluation
    monkeypatch.setattr(trial_evidence, 'save', lambda *a: pytest.fail('legacy archival'))
    result, calls, _ = execute(['invalid', 'measured', 'measured'], enabled=False, protocol=False)
    assert len(calls) == 1 and result['invalid']
    assert 'repeated_trials' not in result
    assert not (tmp_path / 'run/trial-evidence').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'invalid-utf8', 'missing'])
def test_capture_preserves_bytes_or_reports_unavailable_trace_without_following_links(tmp_path, kind):
    path = tmp_path / 'trace.jsonl'
    private = tmp_path / 'private'
    private.write_text('must never be copied')
    if kind == 'symlink':
        path.symlink_to(private)
    elif kind == 'hardlink':
        os.link(private, path)
    elif kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'invalid-utf8':
        path.write_bytes(b'\xff\n')
    output = trial_evidence.capture_output(tmp_path, attempted=True,
        process=subprocess.CompletedProcess([], 7, 'stdout', 'stderr'), error=None)
    assert output['exit_code'] == 7 and output['stdout'] == 'stdout'
    assert 'must never be copied' not in json.dumps(output)
    assert output['trace_status'] == ('captured' if kind == 'invalid-utf8'
                                      else 'missing' if kind == 'missing' else 'unreadable')
    if kind == 'invalid-utf8':
        assert output['trace_bytes_base64'] == '/wo='


def test_timeout_retains_partial_process_output_and_trace(tmp_path):
    (tmp_path / 'trace.jsonl').write_text('partial trace')
    error = subprocess.TimeoutExpired(['actor'], 1, output=b'partial stdout', stderr=b'partial stderr')
    output = trial_evidence.capture_output(tmp_path, attempted=True, process=None, error=error)
    assert output['exit_code'] is None and output['launch_error']['type'] == 'TimeoutExpired'
    assert output['stdout'] == 'partial stdout' and output['stderr'] == 'partial stderr'
    assert output['trace'] == 'partial trace'


@pytest.mark.parametrize('enabled', [False, True])
def test_real_relay_fault_preserves_output_only_when_opted_in(tmp_path, monkeypatch, enabled):
    from test_shared_dataset_runtime import harness, setup_for
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    repo = harness(work, '''from transport import call
(w/'trace.jsonl').write_text('trace before relay failure')
print('actor stdout')
print('actor stderr ' + 'x' * 3000, file=sys.stderr)
call('/run/harnessgrad-tool/tool.sock', 'status')
call('/run/harnessgrad-tool/tool.sock', 'status')
sys.exit(7)
''')
    setup = setup_for(work, record=False)
    program = Path(setup['tool_relay']['program'])
    program.write_text(program.read_text() + '\nraise SystemExit(9)\n')
    verifier = {'kind': 'answer', 'expected': '', 'trial_protocol': PROTOCOL}
    if enabled:
        verifier['trial_evidence'] = {'version': 1}
    else:
        monkeypatch.setattr(trial_evidence, 'capture_output', lambda *a, **kw: pytest.fail('legacy capture'))
    monkeypatch.setattr(runner, '_verify', lambda *a, **kw: pytest.fail('fault reached scorer'))
    result = runner._evaluate(repo, [{'task_id': 't'}], {}, verifiers={'t': verifier},
        setups={'t': setup}, trials=3, run_id='relay-control', harness_sha='actor',
        progress_dir=tmp_path / 'run')
    assert result['invalid']['t']['stage'] == 'tool relay'
    assert result['traces'] == {} and not result['repeated_trials']['complete']
    rows = result['repeated_trials']['results']
    assert len(rows) == 3 and all(row['score'] is None for row in rows)
    if enabled:
        for row in rows:
            record = json.loads((tmp_path / 'run' / row['trial_evidence']['path']).read_text())
            raw = record['result']
            output = raw['trial_evidence_output']
            assert output['trace'] == 'trace before relay failure'
            assert output['stdout'] == 'actor stdout\n'
            assert len(output['stderr']) > 3000 and output['exit_code'] == 7
            assert raw['runtime_services']['tool_relay']['cleanup']['complete']
            assert raw.get('verdict') is None
    else:
        assert all('trial_evidence' not in row for row in rows)


def test_archive_is_not_staged_into_method_channel(evaluation, tmp_path):
    from harnessgrad.channel import _stage_for_method
    execute, _ = evaluation
    result, _, _ = execute(['invalid', 'success', 'invalid'])
    method = tmp_path / 'method'
    method.mkdir()
    _stage_for_method(method, {'round': 2}, result['traces'], result=result,
                      run_dir=tmp_path / 'run', history=[{'round': 1}])
    staged = '\n'.join(p.read_text() for p in (method / '_harnessgrad').rglob('*') if p.is_file())
    assert staged
    assert 'private trace 1' not in staged and 'private grading 1' not in staged
    assert 'trial-evidence/' not in staged
    assert list((tmp_path / 'run/trial-evidence').rglob('*.json'))


def test_existing_archive_cannot_be_overwritten(evaluation, tmp_path):
    execute, _ = evaluation
    result, _, _ = execute(['measured'] * 3)
    row = dict(result['repeated_trials']['results'][0])
    reference = row.pop('trial_evidence')
    path = tmp_path / 'run' / reference['path']
    before = path.read_bytes()
    record = json.loads(before)
    plan = dict(record['binding'], configuration=PROTOCOL)
    with pytest.raises(ValueError, match='already exists'):
        trial_evidence.save(tmp_path / 'run', plan, row, record['result'])
    assert path.read_bytes() == before
