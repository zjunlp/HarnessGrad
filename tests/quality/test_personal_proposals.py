"""B4 consumes recorded plans and freezes scoped candidates without editing projects."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from harnessgrad.improvement import text_edit_proposal
from harnessgrad.improvement.contracts import text_diff
from harnessgrad.personal import records
from harnessgrad.personal.contracts import digest, encode
from harnessgrad.personal.fixtures import ScriptedModelClient
from harnessgrad.personal.model import ModelSession
from harnessgrad.personal.profile import ModelLimits, load_model_settings
from harnessgrad.personal.service import PersonalService
from harnessgrad.personal.workflow import plan_feedback, propose_candidate


KEY = 'synthetic-proposal-credential'
INSTRUCTION = ('AGENTS.md', '# Project\r\n\r\nReport the change.\r\n')
SKILLS = {'skills/review.md': '---\nname: review\ndescription: Review code\n---\n\nCheck the diff.\n'}
EDIT = {'path': 'AGENTS.md', 'old': 'Report the change.', 'new': 'Report the change and actual test results.'}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'AGENTS.md').write_bytes(INSTRUCTION[1].encode())
    service = PersonalService(tmp_path / 'state')
    project_id = service.add_project(root)['id']
    feedback = service.submit_feedback({'project_id': project_id, 'kind': 'explicit_request',
        'text': 'Report the real test results.', 'evidence': [], 'idempotency_key': 'proposal-test',
        'draft_revision': None})
    return service, project_id, feedback['id'], root


def open_session(project, replies, *, limits=None, job_id=None):
    service, project_id, _, _ = project
    settings = load_model_settings({'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1',
        'HG_METHOD_MODEL': 'scripted-proposal', 'HG_METHOD_API_KEY': KEY})
    client = ScriptedModelClient(replies)
    model = ModelSession(service.store, project_id, settings, limits=limits, job_id=job_id,
                         fixture=True, client=client)
    return model, client


def planned(project, replies, *, decision='plan_ready', targets=('AGENTS.md',), limits=None):
    ready = decision == 'plan_ready'
    plan_reply = {'decision': decision, 'intent': 'preference', 'objective': 'Report test results.',
        'reason': 'The user specified a project preference.', 'targets': list(targets) if ready else [],
        'criteria': ['The reply includes observed test results.'] if ready else [],
        'question': 'Which tests?' if decision == 'needs_input' else None,
        'reproduction': {'scenario': 'Next Python edit', 'input': 'Edit the fixture.',
                         'expected': 'Observe test results in the response.'} if ready else None}
    model, client = open_session(project, [plan_reply, *replies], limits=limits)
    plan = plan_feedback(model, *project[1:3], skills=SKILLS, instruction=INSTRUCTION)
    return model, client, plan['record_id']


def candidate(model, project, plan_id, *, instruction=INSTRUCTION):
    return propose_candidate(model, *project[1:3], plan_id, skills=SKILLS, instruction=instruction)


def test_candidate_remains_bound_to_plan_and_reopens_after_completion(project):
    service, _, _, root = project
    model, client, plan_id = planned(project, [{'edits': [EDIT]}], limits=ModelLimits(max_calls=2))
    value = candidate(model, project, plan_id)
    assert value['plan_id'] == plan_id and value['feedback_id'] == project[2]
    assert value['fixture'] is True and value['applied'] is value['verified'] is False
    assert value['files'] == {**SKILLS, 'AGENTS.md': INSTRUCTION[1].replace(EDIT['old'], EDIT['new'])}
    assert '-Report the change.\r\n+Report the change and actual test results.\r\n' in value['diff']
    assert 'skills/review.md' not in value['diff']
    context = json.loads(client.requests[1]['messages'][1]['content'])
    assert context['feedback']['kind'] == 'explicit_request'
    assert context['plan']['intent'] == 'preference'
    assert context['files'] == {INSTRUCTION[0]: INSTRUCTION[1]}
    assert str(root) not in encode(context) and KEY not in encode(value)
    assert (root / 'AGENTS.md').read_bytes() == INSTRUCTION[1].encode()
    assert [call['stage'] for call in model.usage()['calls']] == ['plan', 'proposal']
    assert model.usage()['revisions'] == 1 and model.usage()['transport_attempts'] == 0
    service.store.models.complete(model.job_id)
    reopened, without_calls = open_session(project, [], limits=model.limits, job_id=model.job_id)
    assert candidate(reopened, project, plan_id) == value
    assert not without_calls.requests


def test_selected_skill_can_be_revised_in_place(project):
    path = 'skills/review.md'
    edit = {'path': path, 'old': 'Check the diff.', 'new': 'Check the diff and report the test output.'}
    model, _, plan_id = planned(project, [{'edits': [edit]}], targets=(path,))
    result = candidate(model, project, plan_id)
    assert result['files'][path] == SKILLS[path].replace(edit['old'], edit['new'])
    assert result['files']['AGENTS.md'] == INSTRUCTION[1]
    assert f'--- a/{path}' in result['diff']


def test_new_rule_appends_without_changing_existing_crlf_text(project):
    edit = {'path': 'AGENTS.md', 'old': '', 'new': '## Testing\n\nReport observed results.'}
    model, _, plan_id = planned(project, [{'edits': [edit]}])
    result = candidate(model, project, plan_id)
    assert result['files']['AGENTS.md'] == INSTRUCTION[1] + '\r\n## Testing\r\n\r\nReport observed results.\r\n'


def test_multiline_replacement_keeps_the_existing_line_ending(project):
    edit = {**EDIT, 'old': EDIT['old'] + '\r\n', 'new': EDIT['new'] + '\nKeep the command.\r\n'}
    model, _, plan_id = planned(project, [{'edits': [edit]}])
    result = candidate(model, project, plan_id)
    assert result['files']['AGENTS.md'] == '# Project\r\n\r\n' + EDIT['new'] + '\r\nKeep the command.\r\n'


def test_shared_stage_refuses_replacement_that_splits_a_line_ending():
    reply = {'edits': [{'path': 'instruction.md', 'old': 'first\r', 'new': 'changed'}]}
    with pytest.raises(ValueError, match='split a CRLF'):
        text_edit_proposal({'instruction.md': 'first\r\nsecond\r\n'}, ('instruction.md',),
                           'feedback', prompt='Revise the selected line.', ask=lambda _: reply)


def test_shared_stage_refuses_an_ambiguous_overlapping_anchor():
    reply = {'edits': [{'path': 'instruction.md', 'old': 'aa', 'new': 'b'}]}
    with pytest.raises(ValueError, match='exactly once'):
        text_edit_proposal({'instruction.md': 'aaa'}, ('instruction.md',), 'feedback',
                           prompt='Revise the selected line.', ask=lambda _: reply)


@pytest.mark.parametrize('decision', ['no_change', 'needs_input'])
def test_stopped_plans_do_not_reserve_candidate_work(project, decision):
    model, client, plan_id = planned(project, [], decision=decision)
    with pytest.raises(ValueError, match='ready plan'):
        candidate(model, project, plan_id)
    assert len(client.requests) == 1 and model.usage()['revisions'] == 0


def test_wrong_plan_job_or_changed_surfaces_are_refused_before_model_dispatch(project):
    model, client, plan_id = planned(project, [])
    with pytest.raises(ValueError, match='frozen job'):
        candidate(model, project, plan_id, instruction=('AGENTS.md', 'Different original.'))
    with pytest.raises(ValueError, match='recorded plan'):
        candidate(model, project, 'e' * 64)
    other, other_client = open_session(project, [])
    with pytest.raises(ValueError, match='recorded plan'):
        candidate(other, project, plan_id)
    assert len(client.requests) == 1 and not other_client.requests


@pytest.mark.parametrize('reply', [
    {'edits': [{**EDIT, 'path': 'skills/review.md'}]},
    {'edits': [{**EDIT, 'path': '../AGENTS.md'}]},
    {'edits': [{**EDIT, 'old': 'Absent text'}]},
    {'edits': [{**EDIT, 'old': '', 'new': 'Report the change.'}]},
    {'edits': [{**EDIT, 'new': KEY}]},
    {'edits': [EDIT], 'verified': True},
])
def test_invalid_candidate_is_unpublished_and_charges_the_attempt(project, reply):
    model, client, plan_id = planned(project, [reply], limits=ModelLimits(max_revisions=1))
    with pytest.raises(ValueError):
        candidate(model, project, plan_id)
    with pytest.raises(ValueError, match='candidate revision limit'):
        candidate(model, project, plan_id)
    assert not (project[0].root / 'candidates').exists()
    assert len(client.requests) == 2 and model.usage()['revisions'] == 1
    assert 'candidate.recorded' not in {row['type'] for row in project[0].store.job_events(model.job_id)}


def test_proposal_cannot_reset_the_plan_call_budget(project):
    model, client, plan_id = planned(project, [], limits=ModelLimits(max_calls=1))
    with pytest.raises(ValueError, match='logical model call limit'):
        candidate(model, project, plan_id)
    assert len(client.requests) == 1 and model.usage()['revisions'] == 0


def test_publication_recovery_reuses_candidate_without_new_call_or_revision(project, monkeypatch):
    store = project[0].store
    model, client, plan_id = planned(project, [{'edits': [EDIT]}], limits=ModelLimits(max_calls=2))
    original = store.add_event

    def fail_last_event(db, job_id, kind, payload):
        if kind == 'candidate.recorded':
            raise RuntimeError('injected event failure')
        return original(db, job_id, kind, payload)

    with monkeypatch.context() as patch:
        patch.setattr(store, 'add_event', fail_last_event)
        with pytest.raises(RuntimeError, match='injected event failure'):
            candidate(model, project, plan_id)
    store.models.complete(model.job_id)
    value = candidate(model, project, plan_id)
    assert len(client.requests) == 2 and value['revision'] == model.usage()['revisions'] == 1
    assert sum(row['type'] == 'candidate.recorded' for row in store.job_events(model.job_id)) == 1


def test_committed_legacy_candidate_can_be_read_during_an_unrelated_write(project):
    store = project[0].store
    model, client, plan_id = planned(project, [{'edits': [EDIT]}])
    value = candidate(model, project, plan_id)
    with store.connection() as db:
        db.execute("DELETE FROM evolution_events WHERE job_id=? AND type='candidate.prepared'", (model.job_id,))
        with store.transaction(db):
            assert candidate(model, project, plan_id) == value
    assert len(client.requests) == 2


@pytest.mark.parametrize('legacy', [False, True])
def test_digest_valid_candidate_cannot_forge_diff(project, legacy):
    store = project[0].store
    model, client, plan_id = planned(project, [{'edits': [EDIT]}])
    value = candidate(model, project, plan_id)
    root = store.path.parent / 'candidates' / digest(model.job_id)
    forged = records.read(root, value['record_id'])
    forged['diff'] = 'Invented diff'
    new_id = records.write(root, forged)
    with store.connection() as db:
        if legacy:
            db.execute("DELETE FROM evolution_events WHERE job_id=? AND type='candidate.prepared'", (model.job_id,))
        row = db.execute("SELECT payload FROM evolution_events WHERE job_id=? AND type='candidate.recorded'",
                         (model.job_id,)).fetchone()
        db.execute("UPDATE evolution_events SET payload=? WHERE job_id=? AND type='candidate.recorded'",
                   (encode({**json.loads(row['payload']), 'record_id': new_id}), model.job_id))
    with pytest.raises(ValueError, match='rendered diff' if legacy else 'prepared publication'):
        candidate(model, project, plan_id)
    assert len(client.requests) == 2


def test_recovery_ignores_an_unprepared_candidate_after_an_invalid_reply(project):
    store = project[0].store
    model, client, plan_id = planned(project, [{'edits': []}, {'edits': [EDIT]}])
    with pytest.raises(ValueError, match='one to four edits'):
        candidate(model, project, plan_id)
    request = next(event['payload'] for event in store.job_events(model.job_id)
                   if event['type'] == 'candidate.requested')
    before = {**SKILLS, INSTRUCTION[0]: INSTRUCTION[1]}
    files = {**before, INSTRUCTION[0]: 'Unprepared text.\r\n'}
    fabricated = {'schema': 'harnessgrad.personal.candidate.v1', **request, 'job_id': model.job_id,
                  'project_id': project[1], 'feedback_id': project[2], 'fixture': True,
                  'applied': False, 'verified': False, 'stage': 'text-edit-v1', 'revision': 1,
                  'files': files, 'diff': text_diff(before, files)}
    root = store.path.parent / 'candidates' / digest(model.job_id)
    fabricated_id = records.write(root, fabricated)
    value = candidate(model, project, plan_id)
    assert value['revision'] == 2 and value['record_id'] != fabricated_id
    assert len(client.requests) == 3 and records.read(root, fabricated_id) == fabricated


def test_missing_prepared_publication_can_retry_with_remaining_budget(project, monkeypatch):
    model, client, plan_id = planned(project, [{'edits': [EDIT]}, {'edits': [EDIT]}])
    with monkeypatch.context() as patch:
        def fail_write(*args):
            raise OSError('injected record write failure')

        patch.setattr(records, 'write', fail_write)
        with pytest.raises(OSError, match='injected record write failure'):
            candidate(model, project, plan_id)
    value = candidate(model, project, plan_id)
    assert value['revision'] == 2 and len(client.requests) == 3
    assert candidate(model, project, plan_id) == value


def test_two_candidate_requests_share_the_same_job_lock(project, monkeypatch):
    started, release = Event(), Event()
    model, client, plan_id = planned(project, [{'edits': [EDIT]}])
    second, inactive_client = open_session(project, [], job_id=model.job_id)
    complete = client.complete

    def wait_to_complete(**kwargs):
        started.set()
        if not release.wait(5):
            raise RuntimeError('test executor wait timed out')
        return complete(**kwargs)

    monkeypatch.setattr(client, 'complete', wait_to_complete)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(candidate, model, project, plan_id)
        try:
            assert started.wait(5)
            with pytest.raises(ValueError, match='already has an executor'):
                candidate(second, project, plan_id)
        finally:
            release.set()
        result = running.result(timeout=5)
    assert candidate(second, project, plan_id) == result
    assert len(client.requests) == 2 and not inactive_client.requests
