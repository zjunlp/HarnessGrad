"""Expected launcher failures archive finally-captured output, then still raise."""
import json
import hashlib

import pytest

from eval.run import runner
from test_declared_trials_continue import PROTOCOL
from test_shared_dataset_runtime import harness


@pytest.mark.parametrize('protocol', [False, True])
@pytest.mark.parametrize('failure', ['missing-executable', 'sandbox-unavailable'])
def test_launch_failure_keeps_original_exception_and_private_archive(tmp_path, monkeypatch, protocol, failure):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    repo = harness(work, "raise AssertionError('actor must never launch')")
    missing = tmp_path / 'missing-bwrap'
    def bwrap():
        if failure == 'sandbox-unavailable':
            raise runner.sandbox_mod.SandboxUnavailable('fixture sandbox unavailable')
        return str(missing)
    monkeypatch.setattr(runner.sandbox_mod, '_bwrap', bwrap)
    # The real sandbox command and Popen path produce ENOENT, while the other
    # case exercises the existing SandboxUnavailable -> SystemExit conversion.
    spec = {'kind': 'answer', 'expected': 'yes'}
    if protocol:
        spec['trial_protocol'] = PROTOCOL
    error_type = FileNotFoundError if failure == 'missing-executable' else SystemExit
    with pytest.raises(error_type) as plain:
        runner.run_one(repo, {'task_id': 'fixture/t'}, verifier=spec)
    spec['trial_evidence'] = {'version': 1}
    with pytest.raises(error_type) as archived:
        runner.evaluate(repo, [{'task_id': 'fixture/t'}], {}, verifiers={'fixture/t': spec},
            trials=3, run_id='fixture', run_seed='ordinary-seed', harness_sha='fixture',
            progress_dir=tmp_path / 'run')
    if failure == 'missing-executable':
        assert archived.value.errno == plain.value.errno == 2
    else:
        assert str(archived.value) == str(plain.value)
    records = [json.loads(path.read_text()) for path in (tmp_path / 'run/trial-evidence').rglob('*.json')]
    archives = [record for record in records if record.get('schema') == 'harnessgrad.trial-evidence.v1']
    assert len(archives) == 1
    record = archives[0]
    assert record['binding'] == {'run_id': 'fixture', 'round': 0, 'harness_sha': 'fixture',
        'task_id': 'fixture/t', 'trial_id': 'round-0.harness-fixture.trial-1',
        'seed': str(PROTOCOL['seeds'][0]) if protocol else 'ordinary-seed'}
    assert record['outcome']['status'] == 'raised'
    assert record['outcome']['exception_type'] == error_type.__name__
    assert record['result'] == archived.value.trial_evidence_result
    reference = archived.value.trial_evidence
    raw = (tmp_path / 'run' / reference['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference['sha256']
    assert json.loads(raw) == record
    output = record['result']['trial_evidence_output']
    assert output['launch_attempted'] and output['exit_code'] is None
    assert output['launch_error']['type'] == ('FileNotFoundError' if failure == 'missing-executable'
                                               else 'SandboxUnavailable')
    assert output['trace_status'] == output['answer_status'] == 'missing'
    assert list((tmp_path / 'run').rglob('in-flight/*.json'))
    assert not list((tmp_path / 'run').rglob('index.json'))
    assert not list((tmp_path / 'run').rglob('repeated-trials.json'))


def test_failure_archive_write_error_is_visible(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    repo = harness(work, "raise AssertionError('actor must never launch')")
    monkeypatch.setattr(runner.sandbox_mod, '_bwrap', lambda: str(tmp_path / 'missing-bwrap'))
    def fail(*args, **kwargs):
        raise OSError('fixture archive disk full')
    monkeypatch.setattr(runner.trial_evidence, 'save', fail)
    with pytest.raises(OSError, match='archive disk full') as raised:
        runner.evaluate(repo, [{'task_id': 'fixture/t'}], {},
            verifiers={'fixture/t': {'kind': 'answer', 'expected': 'yes', 'trial_evidence': {'version': 1}}},
            run_id='fixture', harness_sha='fixture', progress_dir=tmp_path / 'run')
    assert isinstance(raised.value.__context__, FileNotFoundError)
    assert list((tmp_path / 'run').rglob('in-flight/*.json'))
