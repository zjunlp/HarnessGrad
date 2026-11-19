"""Offline coverage through the production service, worker and Codex adapter."""
from contextlib import contextmanager
import http.client
import json
import sqlite3
from threading import Event, Thread
import time

import pytest

from harnessgrad.personal.adapters import codex
from harnessgrad.personal.api import PersonalServer
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.fixtures import ScriptedModelClient, example
from harnessgrad.personal.profile import load_model_settings
from harnessgrad.personal.service import PersonalService

SETTINGS = {
    'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1', 'HG_METHOD_MODEL': 'offline-proposal',
    'HG_METHOD_API_KEY': 'never-used-by-scripted-client',
}
PLAN = {'decision': 'plan_ready', 'intent': 'preference', 'objective': 'Report focused test results',
        'reason': 'Explicitly requested', 'targets': ['instruction.md'],
        'criteria': ['Response lists the tests run'], 'question': None,
        'reproduction': {'scenario': 'Review a patch', 'input': 'Review the change', 'expected': 'Report tests'}}
EDIT = {'edits': [{'path': 'instruction.md', 'old': 'Run focused tests.',
                   'new': 'Run focused tests and report the results.'}]}
QUESTION_PLAN = {**PLAN, 'decision': 'needs_input', 'targets': [], 'criteria': [],
                 'question': 'Which test suite should run?', 'reproduction': None}
requires_safe_read = pytest.mark.skipif(not codex._secure_access_available(), reason='B5 safe selected read unavailable')


@contextmanager
def running(service):
    server = PersonalServer(service)
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()

    def request(path, value=None, *, authorized=True, cursor=None):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + server.token
        if cursor is not None:
            headers['Last-Event-ID'] = str(cursor)
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET' if value is None else 'POST', '/api/personal/' + path,
                               body=None if value is None else json.dumps(value), headers=headers)
            response = connection.getresponse()
            text = response.read().decode('utf-8')
            return response.status, text if response.headers['Content-Type'].startswith('text/event-stream') else json.loads(text)
        finally:
            connection.close()

    try:
        yield request
    finally:
        service.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def setup(tmp_path, client=None):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_bytes(b'Run focused tests.\r\n')
    service = PersonalService(tmp_path / 'state', proposal_client=client,
                              proposal_settings=load_model_settings(SETTINGS) if client is not None else None)
    return service, service.add_project(project)['id'], project / 'AGENTS.md'


def submit(request, project, key='request-1'):
    status, feedback = request('feedback', {'project_id': project, 'kind': 'explicit_request',
        'text': 'Please report the tests that ran.', 'evidence': [], 'idempotency_key': key, 'draft_revision': None})
    assert status == 200
    return f'projects/{project}/feedback/{feedback["id"]}/proposal'


def terminal(request, route):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        status, body = request(route)
        assert status == 200
        run = body['proposal']
        if run['status'] in ('ready', 'failed', 'interrupted', 'needs_input', 'no_change', 'cancelled'):
            return run
        time.sleep(0.02)
    pytest.fail('proposal did not finish')


@requires_safe_read
def test_http_feedback_to_native_diff_is_idempotent_scoped_and_read_only(tmp_path):
    entered, release = Event(), Event()

    class PausedModel(ScriptedModelClient):
        def complete(self, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().complete(**kwargs)

    client = PausedModel([PLAN, EDIT])
    service, project, file = setup(tmp_path, client)
    original = file.read_bytes()
    other = tmp_path / 'other'; other.mkdir()
    other_id = service.add_project(other)['id']
    with running(service) as request:
        route, second = submit(request, project), submit(request, project, 'request-2')
        assert request(route, {}, authorized=False)[0] == 401
        assert request(route.replace(project, other_id), {})[0] == 400
        assert request(route, {'path': 'other.md'})[0] == 400
        assert request(route) == (200, {'proposal': None})
        assert not entered.is_set()
        assert request(route, {})[0] == 200
        try:
            assert entered.wait(4)
            assert request(route, {})[1]['proposal']['status'] == 'planning'
            assert request(second, {})[1]['code'] == 'proposal_busy'
            assert request(second) == (200, {'proposal': None})
        finally:
            release.set()
        run = terminal(request, route)
        assert run['status'] == 'ready', run
        result = run['result']
        assert result['paths'] == ['AGENTS.md']
        assert '+++ b/project/AGENTS.md' in result['diff']
        assert '+Run focused tests and report the results.' in result['diff']
        assert result['fixture'] and not result['applied'] and not result['verified']
        assert request(route, {})[1]['proposal'] == run
        usage = service.store.models.usage(run['job_id'])
        assert usage['logical_calls'] == 2 and usage['transport_attempts'] == 0
    with running(PersonalService(service.root)) as read_only:
        assert read_only(route)[1]['proposal'] == run
        assert read_only(route, {})[1]['proposal'] == run
    assert file.read_bytes() == original and len(client.requests) == 2


def test_opt_in_and_platform_unavailability_do_not_call_model(tmp_path, monkeypatch):
    client = ScriptedModelClient([PLAN, EDIT])
    monkeypatch.setattr(codex, '_secure_access_available', lambda: False)
    service, project, _ = setup(tmp_path, client)
    with running(service) as request:
        assert request('health')[1]['proposal_generation'] == {'status': 'unavailable'}
        route = submit(request, project)
        assert request(route, {})[1]['code'] == 'proposal_unavailable'
    with running(PersonalService(service.root)) as request:
        assert request(route, {})[1]['code'] == 'proposal_unconfigured'
    assert not client.requests


@requires_safe_read
def test_needs_input_is_shown_without_a_candidate_or_second_model_call(tmp_path):
    client = ScriptedModelClient([QUESTION_PLAN])
    service, project, _ = setup(tmp_path, client)
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        run = terminal(request, route)
        assert run['status'] == 'needs_input', run
        assert run['result']['question'] == QUESTION_PLAN['question'] and run['result']['fixture']
        assert len(run['result']['plan_id']) == 64 and 'diff' not in run['result']
        assert service.store.models.usage(run['job_id'])['status'] == 'paused'
    assert len(client.requests) == 1


@requires_safe_read
def test_drift_during_model_generation_never_publishes_native_diff(tmp_path):
    class DriftModel(ScriptedModelClient):
        def complete(self, **kwargs):
            result = super().complete(**kwargs)
            if len(self.requests) == 2:
                file.write_bytes(b'Concurrent edit.\n')
            return result

    service, project, file = setup(tmp_path, DriftModel([PLAN, EDIT]))
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        run = terminal(request, route)
        assert run['status'] == 'failed'
        assert run['result'] == {'code': 'stale_snapshot'}
    assert file.read_bytes() == b'Concurrent edit.\n'


def test_reopened_service_marks_abandoned_requests_interrupted_without_replay(tmp_path):
    service, project, _ = setup(tmp_path)
    with running(service) as request:
        route = submit(request, project)
        feedback = route.split('/')[-2]
        service.store.proposal_runs.begin(project, feedback, 'prior-owner')
    with running(PersonalService(service.root)) as request:
        run = request(route)[1]['proposal']
        assert run['status'] == 'interrupted'
        assert request(route, {})[1]['proposal'] == run


def test_v4_to_v6_preserves_feedback_evidence_drafts_and_search(tmp_path):
    service, project, _ = setup(tmp_path)
    service.import_session(project, example())
    service.store.save_draft(project, 'original draft', 0)
    with running(service) as request:
        route = submit(request, project)
        feedback = route.split('/')[-2]
        receipt = service.store.feedback.get(project, feedback)
    path = service.store.path
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE proposal_events')
        db.execute('DROP TABLE proposal_runs')
        db.execute('PRAGMA user_version=4')
    migrated = EventStore(path)
    assert migrated.feedback.get(project, feedback) == receipt
    assert migrated.draft(project)['text'] == 'original draft'
    assert migrated.evidence.fts == service.store.evidence.fts
    assert migrated.proposal_runs.get(project, feedback, None) is None
    with migrated.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
        assert not db.execute('PRAGMA foreign_key_check').fetchall()



def events(request, route, cursor=None):
    status, wire = request(route + '/events', cursor=cursor)
    assert status == 200
    return [(int(frame[0].removeprefix('id: ')), json.loads(frame[2].removeprefix('data: '))['status'])
            for lines in wire.strip().split('\n\n') if (frame := lines.splitlines()) and frame[0].startswith('id: ')]


@requires_safe_read
def test_http_reply_resumes_original_job_and_stream_replays_only_new_events(tmp_path):
    client = ScriptedModelClient([QUESTION_PLAN, PLAN, EDIT])
    service, project, file = setup(tmp_path, client)
    original = file.read_bytes()
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        paused = terminal(request, route)
        answer = {'question_id': paused['result']['question_id'], 'answer': 'Run the Python unit tests.'}
        initial = events(request, route)
        assert initial == [(1, 'collecting'), (2, 'planning'), (3, 'needs_input')]
        assert request(route + '/events', authorized=False)[0] == 401
        assert request(route + '/events', cursor='-1')[0] == 400
        assert request(route + '/reply', {**answer, 'question_id': '0' * 64})[0] == 409
        assert request(route + '/reply', answer)[0] == 200
        run = terminal(request, route)
        assert run['status'] == 'ready' and run['job_id'] == paused['job_id'], run
        assert not run['result']['applied'] and not run['result']['verified']
        resumed = [(4, 'collecting'), (5, 'planning'), (6, 'proposing'), (7, 'checking'), (8, 'ready')]
        assert events(request, route, cursor=initial[-1][0]) == resumed
        assert not events(request, route, cursor=resumed[-1][0])
        assert request(route + '/reply', answer)[1]['proposal'] == run
        assert request(route + '/reply', {**answer, 'answer': 'Different answer'})[0] == 409
        assert events(request, route) == initial + resumed
        usage = service.store.models.usage(run['job_id'])
        assert usage['logical_calls'] == 3 and usage['transport_attempts'] == 0
        clarification = {'question': QUESTION_PLAN['question'], 'response': answer['answer']}
        for call in client.requests[1:]:
            assert json.loads(call['messages'][-1]['content'])['clarification'] == clarification
    assert file.read_bytes() == original and len(client.requests) == 3


@requires_safe_read
def test_repeated_question_ends_without_a_second_open_question(tmp_path):
    follow_up = {**QUESTION_PLAN, 'question': 'What is the exact test command?'}
    client = ScriptedModelClient([QUESTION_PLAN, follow_up])
    service, project, file = setup(tmp_path, client)
    original = file.read_bytes()
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        paused = terminal(request, route)
        answer = {'question_id': paused['result']['question_id'], 'answer': 'Python unit tests.'}
        assert request(route + '/reply', answer)[0] == 200
        run = terminal(request, route)
        assert run['status'] == 'failed' and run['job_id'] == paused['job_id'], run
        assert run['result']['code'] == 'clarification_exhausted'
        assert run['result']['question'] == follow_up['question']
        assert run['result']['question_id'] is None and 'diff' not in run['result']
        assert events(request, route, cursor=3) == [(4, 'collecting'), (5, 'planning'), (6, 'failed')]
        with service.store.connection() as db:
            questions = db.execute('SELECT id,status FROM questions WHERE job_id=?', (run['job_id'],))
            assert [tuple(row) for row in questions] == [(answer['question_id'], 'answered')]
        assert request(route + '/reply', answer)[1]['proposal'] == run
    assert file.read_bytes() == original and len(client.requests) == 2


@requires_safe_read
def test_cancel_before_candidate_reservation_prevents_a_new_call(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    client = ScriptedModelClient([PLAN, EDIT])
    service, project, file = setup(tmp_path, client)
    original = file.read_bytes()
    reserve = service.store.models.reserve

    def pause_before_reserve(job_id, lease, stage, *args, **kwargs):
        if stage == 'proposal':
            entered.set()
            assert release.wait(5)
        return reserve(job_id, lease, stage, *args, **kwargs)

    monkeypatch.setattr(service.store.models, 'reserve', pause_before_reserve)
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        try:
            assert entered.wait(4)
            thread = service.proposals._thread
            status, body = request(route + '/cancel', {})
            assert status == 200 and body['proposal']['status'] == 'cancelled'
        finally:
            release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert terminal(request, route) == body['proposal']
        assert events(request, route)[-2:] == [(3, 'proposing'), (4, 'cancelled')]
        usage = service.store.models.usage(body['proposal']['job_id'])
        assert [call['stage'] for call in usage['calls']] == ['plan']
        assert usage['transport_attempts'] == 0
    assert file.read_bytes() == original and len(client.requests) == 1


@requires_safe_read
def test_cancel_during_owned_call_stops_later_stages(tmp_path):
    entered, release = Event(), Event()

    class PausedModel(ScriptedModelClient):
        def complete(self, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().complete(**kwargs)

    client = PausedModel([PLAN, EDIT])
    service, project, file = setup(tmp_path, client)
    original = file.read_bytes()
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        try:
            assert entered.wait(4)
            thread = service.proposals._thread
            status, body = request(route + '/cancel', {})
            assert status == 200 and body['proposal']['status'] == 'cancelled'
            assert request(route + '/cancel', {}) == (status, body)
        finally:
            release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert terminal(request, route) == body['proposal']
        assert events(request, route) == [(1, 'collecting'), (2, 'planning'), (3, 'cancelled')]
        assert service.store.models.usage(body['proposal']['job_id'])['logical_calls'] == 1
    assert file.read_bytes() == original and len(client.requests) == 1


@requires_safe_read
def test_reply_refuses_changed_snapshot_before_another_model_call(tmp_path):
    client = ScriptedModelClient([QUESTION_PLAN, PLAN, EDIT])
    service, project, file = setup(tmp_path, client)
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        paused = terminal(request, route)
        file.write_text('User updated this instruction.\n', encoding='utf-8')
        assert request(route + '/reply', {'question_id': paused['result']['question_id'], 'answer': 'Unit tests.'})[0] == 200
        run = terminal(request, route)
        assert run['status'] == 'failed' and run['result'] == {'code': 'stale_snapshot'}
    assert len(client.requests) == 1


@requires_safe_read
def test_v5_migration_preserves_ready_receipt_and_backfills_event(tmp_path):
    service, project, _ = setup(tmp_path, ScriptedModelClient([PLAN, EDIT]))
    with running(service) as request:
        route = submit(request, project)
        assert request(route, {})[0] == 200
        ready = terminal(request, route)
        assert ready['status'] == 'ready'
    with sqlite3.connect(service.store.path) as db:
        db.execute('DROP TABLE proposal_events')
        db.execute('ALTER TABLE proposal_runs DROP COLUMN snapshot_sha256')
        db.execute('PRAGMA user_version=5')
    with running(PersonalService(service.root)) as request:
        assert request(route)[1]['proposal'] == ready
        assert events(request, route) == [(1, 'ready')]
