"""B2 feedback, scoped search and excerpts; synthetic sources and loopback only."""
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import json
import sqlite3
import threading

import pytest

from harnessgrad.personal.api import PersonalServer
from harnessgrad.personal.contracts import digest, encode
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.feedback_store import FeedbackConflict
from harnessgrad.personal.fixtures import example
from harnessgrad.personal.service import PersonalService


@pytest.fixture
def selected(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    (project / 'keep.txt').write_text('original')
    service = PersonalService(tmp_path / 'state')
    identifier = service.add_project(project)['id']
    service.import_session(identifier, example())
    return service, identifier, project


def page(service, project, **options):
    session = service.store.sessions(project)[0]
    return service.store.evidence.search(project, session['source_instance'], session['session_id'], **options)


def payload(service, project, **changes):
    row = page(service, project)['events'][-1]
    return {'project_id': project, 'kind': 'explicit_request', 'text': '以后修改 Python 后先运行测试。',
            'evidence': [{key: row[key] for key in ('source_instance', 'session_id', 'event_id', 'sha256')}
                         | {'start': 0, 'end': len(row['text'])}],
            'idempotency_key': 'request-one', 'draft_revision': None, **changes}


def test_feedback_is_explicit_persistent_idempotent_and_not_a_model_job(selected):
    service, project, root = selected
    value = payload(service, project); original = copy.deepcopy(value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(service.submit_feedback, [value] * 8))
    assert all(receipt == receipts[0] for receipt in receipts)
    receipt = receipts[0]
    assert value == original and receipt['origin'] == 'user' and receipt['status'] == 'recorded'
    assert receipt['evidence'][0]['source'] == 'synthetic'
    reopened = PersonalService(service.root)
    assert reopened.store.feedback.get(project, receipt['id']) == receipt
    assert len(reopened.store.feedback.list(project)) == 1
    assert reopened.submit_feedback({**value, 'idempotency_key': 'another-request'})['id'] != receipt['id']
    with pytest.raises(FeedbackConflict): service.submit_feedback({**value, 'text': 'changed'})
    assert service.snapshot()['feedback_count'] == 2
    assert service.snapshot()['model_calls'] == service.snapshot()['harness_changes'] == 0
    with service.store.connection() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1  # Original import only.
        assert db.execute('SELECT count(*) FROM source_events').fetchone()[0] == 5
    assert (root / 'keep.txt').read_text() == 'original'


def test_draft_binding_and_requests_without_history(selected):
    service, project, root = selected
    value = payload(service, project, evidence=[])
    draft = service.store.save_draft(project, value['text'], 0)
    value['draft_revision'] = draft['revision']
    first = service.submit_feedback(value)
    assert first['missing_context'] and first['kind'] == 'explicit_request'
    service.store.save_draft(project, 'A later local edit', draft['revision'])
    assert service.submit_feedback(value) == first  # Retry remains valid after later editing.
    with pytest.raises(FeedbackConflict):
        service.submit_feedback({**value, 'idempotency_key': 'new-but-stale'})
    other = root.parent / 'empty'; other.mkdir()
    other_id = service.add_project(other)['id']
    assert service.submit_feedback({**value, 'project_id': other_id, 'draft_revision': None})['missing_context']
    assert not service.store.sessions(other_id)
    with pytest.raises(ValueError): service.store.feedback.get(other_id, first['id'])


@pytest.mark.parametrize('role,provenance', [('user', 'user_quote'), ('tool', 'tool_observation'), ('assistant', 'assistant_output')])
def test_unicode_excerpt_and_incomplete_sources_keep_their_original_provenance(selected, role, provenance):
    service, project, _ = selected
    source = {**example()['events'][0], 'event_id': 'unicode', 'text': '前😀先跑测试后', 'role': role, 'truncated': True}
    service.import_session(project, {'schema': example()['schema'], 'events': [source]})
    row = next(row for row in page(service, project)['events'] if row['event_id'] == 'unicode')
    value = payload(service, project)
    value['evidence'] = [{key: row[key] for key in ('source_instance', 'session_id', 'event_id', 'sha256')}
                         | {'start': 2, 'end': 6}]
    receipt = service.submit_feedback(value)
    item = receipt['evidence'][0]
    assert item['text'] == '先跑测试' and item['provenance'] == provenance
    assert item['partial'] and item['truncated'] and item['context_incomplete'] and receipt['missing_context']
    assert item['sha256'] == digest(source)
    assert source['text'] == '前😀先跑测试后'


@pytest.mark.parametrize('change', ['digest', 'session', 'project', 'offset', 'boolean', 'duplicate', 'forged_text', 'kind', 'empty', 'large'])
def test_invalid_feedback_never_commits_partial_evidence(selected, change):
    service, project, root = selected
    value = payload(service, project)
    if change == 'digest': value['evidence'][0]['sha256'] = 'a' * 64
    if change == 'session': value['evidence'][0]['session_id'] = 'other-session'
    if change == 'project':
        other = root.parent / 'other'; other.mkdir(); value['project_id'] = service.add_project(other)['id']
    if change == 'offset': value['evidence'][0]['end'] = 100000
    if change == 'boolean': value['evidence'][0]['start'] = False
    if change == 'duplicate': value['evidence'] *= 2
    if change == 'forged_text': value['evidence'][0]['text'] = 'invented observation'
    if change == 'kind': value['kind'] = 'model_inference'
    if change == 'empty': value['text'] = '  '
    if change == 'large': value['text'] = '字' * 6000
    with pytest.raises(ValueError): service.submit_feedback(value)
    with service.store.connection() as db:
        assert db.execute('SELECT count(*) FROM feedback').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM feedback_evidence').fetchone()[0] == 0


@pytest.mark.parametrize('fts', [True, False])
def test_search_keeps_tail_literal_query_and_exact_project_session_window(selected, fts):
    service, project, root = selected
    service.store.evidence.fts = fts and service.store.evidence.fts
    base = example()['events'][0]
    events = [{**base, 'event_id': f'long-{i}', 'session_id': 'long', 'text': f'ordinary {i}'} for i in range(200)]
    events[-2]['text'] = '纠正：修改后先跑测试；abc "quote" OR NEAR(*)'
    events[-1].update(text='最后结果：测试完成', role='tool', truncated=True)
    service.import_session(project, {'schema': example()['schema'], 'events': events})
    source = base['source_instance']
    search = lambda **args: service.store.evidence.search(project, source, 'long', **args)
    recent = search(); assert len(recent['events']) == 50 and recent['events'][-1]['event_id'] == 'long-199'
    assert recent['events'][-1]['truncated'] and recent['has_more']
    older = search(before=recent['next_before'])
    assert len(older['events']) == 50 and older['events'][-1]['seq'] < recent['events'][0]['seq']
    seq = search(query='先跑测试')['events'][0]['seq']
    assert search(query='先跑测试', after=seq)['events'] == []
    assert len(search(query='"quote" OR NEAR(*)')['events']) == 1
    assert len(search(query='测试')['events']) == 2 and search(query='测试')['mode'] == 'session_filter'
    other = root.parent / 'other'; other.mkdir(); second = service.add_project(other)['id']
    service.import_session(second, {'schema': example()['schema'], 'events': [{**events[-1], 'text': 'foreign marker'}]})
    assert not search(query='foreign marker')['events']
    with pytest.raises(ValueError): service.store.evidence.search(project, source, 'not-selected')
    assert search(query='ordinary')['mode'] == ('fts5' if service.store.evidence.fts else 'session_filter')


def test_missing_fts_extension_falls_back_without_losing_original_sources(tmp_path, monkeypatch):
    original = sqlite3.connect
    class WithoutFTS(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith('CREATE VIRTUAL TABLE'): raise sqlite3.OperationalError('no such module: fts5')
            return super().execute(sql, *args)
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: original(*a, factory=WithoutFTS, **kw))
    service = PersonalService(tmp_path / 'state'); project = service.store.add_project(tmp_path / 'project')['id']
    service.import_session(project, example())
    assert not service.store.evidence.fts
    assert len(page(service, project)['events']) == 5
    assert page(service, project, query='does not exist')['mode'] == 'session_filter'


def test_index_backfill_import_rollback_and_evidence_budget(selected, monkeypatch):
    service, project, _ = selected
    query = example()['events'][0]['text']
    assert page(service, project, query=query)['events']
    changed = example(); changed['events'][0]['event_id'] = 'rolled-back'; changed['events'][0]['text'] = 'rollback-marker'
    changed['events'][1]['text'] = 'conflicting preserved evidence'
    with pytest.raises(ValueError): service.import_session(project, changed)
    assert page(service, project, query='rollback-marker')['events'] == []
    import harnessgrad.personal.feedback_store as module
    monkeypatch.setattr(module, 'MAX_EVIDENCE_BYTES', 1)
    with pytest.raises(ValueError): service.submit_feedback(payload(service, project))
    assert service.store.feedback.count() == 0
    # Rebuild the optional cache from authoritative rows, including older imported text.
    with service.store.connection() as db, service.store.transaction(db): db.execute('DROP TABLE personal_source_fts')
    reopened = PersonalService(service.root)
    assert page(reopened, project, query=query)['events']


def test_loopback_feedback_requires_auth_strict_fields_and_project_ownership(selected):
    service, project, _ = selected
    server = PersonalServer(service); worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    def request(path, value=None, token=None):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            client.request('GET' if value is None else 'POST', '/api/personal/' + path,
                body=None if value is None else encode(value).encode('utf-8'),
                headers={'Authorization': 'Bearer ' + (token or server.token), 'Content-Type': 'application/json'})
            response = client.getresponse(); return response.status, json.loads(response.read())
        finally: client.close()
    try:
        value = payload(service, project)
        assert request('feedback', value, token='wrong')[0] == 401
        assert request('feedback', {**value, 'approved': True})[0] == 400
        status, receipt = request('feedback', value); assert status == 200
        assert request(f'projects/{project}/feedback/{receipt["id"]}')[1] == receipt
        assert len(request(f'projects/{project}/feedback')[1]['feedback']) == 1
        status, conflict = request('feedback', {**value, 'text': 'sensitive changed text'})
        assert status == 409 and conflict['code'] == 'feedback_conflict' and 'sensitive' not in json.dumps(conflict)
        assert request(f'projects/{project}/evidence')[0] == 400
        session = service.store.sessions(project)[0]
        path = f'projects/{project}/evidence?source_instance={session["source_instance"]}&session_id={session["session_id"]}'
        assert request(path)[1]['events']
        assert request(path + '&limit=51')[0] == 400
        assert request(path + '&after=1&after=2')[0] == 400
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)
        assert not worker.is_alive()
