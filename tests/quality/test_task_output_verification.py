"""Safe output bytes and private verifier forwarding stay distinct from actor payloads."""
from copy import deepcopy
import subprocess
from types import SimpleNamespace

import pytest

from eval.run.task_output import TaskOutputCapture
from eval.run.task_session import TaskSession
from eval.run.task_verification import VerifySpec, verify_output, with_tool_recording
from eval.run.task_workspace import TaskWorkspace


def test_safe_archive_retains_raw_newlines_while_feedback_is_normalized(tmp_path):
    (tmp_path / 'answer.txt').write_bytes(b'answer\r\nline\r')
    (tmp_path / 'trace.jsonl').write_bytes(b'{"step":1}\r\n')
    evidence = {}
    capture = TaskOutputCapture(tmp_path, evidence)
    capture.attempted = True
    capture.process = subprocess.CompletedProcess([], 0, b'raw stdout', b'raw stderr')
    output = capture.collect(stepwise=False)
    assert output.answer == 'answer\nline\n' and output.trace == '{"step":1}\n'
    assert evidence['answer'] == 'answer\r\nline\r' and evidence['trace'] == '{"step":1}\r\n'
    assert evidence['launch_attempted'] is True and evidence['stdout'] == 'raw stdout'
    original = deepcopy(evidence)
    (tmp_path / 'answer.txt').write_text('later mutation')
    capture.capture_pending()
    assert evidence == original


def test_pending_capture_keeps_launch_exception_without_following_actor_alias(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'sentinel').write_text('must not read')
    (tmp_path / 'answer.txt').symlink_to(private / 'sentinel')
    evidence = {}
    capture = TaskOutputCapture(tmp_path, evidence)
    capture.attempted = True
    capture.error = OSError('authored launch failure')
    capture.capture_pending()
    assert evidence['launch_error'] == {'type': 'OSError', 'detail': 'authored launch failure'}
    assert evidence['answer_status'] != 'captured'
    assert 'must not read' not in str(evidence)


def test_private_verify_spec_forwards_only_to_canonical_verifier_and_returns_same_value(tmp_path):
    private = {'kind': 'command', 'inputs': [{'path': 'private', 'content': 'GOLD_SENTINEL'}]}
    environment = {'kind': 'files'}
    services, running = [{'name': 'trusted'}], object()
    spec = VerifySpec(private, environment, True, 9, 't', 'r', 'trial-1', ())
    calls = []
    verdict = {'passed': True, 'score': 0.5}

    def verify(*args, **kwargs):
        calls.append((args, kwargs))
        return verdict

    assert verify_output(tmp_path, spec, 'answer', service_specs=services, running=running, verify=verify) is verdict
    assert calls == [((tmp_path, private, 'answer'), {
        'sandbox': True, 'task_id': 't', 'run_id': 'r', 'trial_id': 'trial-1', 'timeout_s': 9,
        'env': environment, 'service_specs': services, 'running': running, 'immutable_runtimes': ()})]
    assert spec.declaration is private


def test_recording_input_is_added_privately_without_mutating_original_or_following_target_alias(tmp_path):
    workdir = tmp_path / 'task'
    workdir.mkdir()
    source = tmp_path / 'sealed'
    source.mkdir()
    (source / 'record.json').write_text('authored')
    entry = {'path': 'recording', 'from': str(source), 'immutable': True}
    tool = SimpleNamespace(recording_input=lambda **kwargs: entry)
    original = {'kind': 'command', 'inputs': [{'path': 'gold', 'content': 'PRIVATE'}]}
    before = deepcopy(original)
    updated = with_tool_recording(workdir, original, tool, archive_root=tmp_path)
    assert updated['inputs'] == [*original['inputs'], entry] and original == before
    (workdir / 'recording').rmdir()
    (workdir / 'recording').symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        with_tool_recording(workdir, original, tool, archive_root=tmp_path)
    assert (source / 'record.json').read_text() == 'authored'


def test_usage_and_gateway_evidence_are_read_while_backends_are_alive():
    workspace = TaskWorkspace()
    session = TaskSession(workspace)
    events = []
    session.services = SimpleNamespace(usage=lambda: events.append('services-usage') or {'reported': 'service'})
    session.tool = SimpleNamespace(model=SimpleNamespace(report=lambda: events.append('model-report') or {'raw': True}))
    session.model = SimpleNamespace(target=('127.0.0.1', 1), account=lambda: events.append('account') or {'connections': 2})
    try:
        result = session.environment_usage(project_model=lambda report: events.append('project') or {'projected': report})
        assert result == {'projected': {'raw': True}}
        assert session.model_evidence() == {'published': True, 'target': '127.0.0.1:1', 'connections': 2}
        assert events == ['services-usage', 'model-report', 'project', 'account']
    finally:
        workspace.remove_task()
