"""Stable project drafts, synthetic loopback only; no models or harness writes."""
import http.client
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from harnessgrad.personal.api import PersonalServer
from harnessgrad.personal.contracts import MAX_DRAFT_BYTES
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.service import PersonalService

ROOT = Path(__file__).resolve().parents[2]


def request(server, path, value=None, *, token=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (token or server.token)}
    try:
        client.request('GET' if value is None else 'POST', '/api/personal/' + path,
                       body=None if value is None else json.dumps(value), headers=headers)
        response = client.getresponse()
        return response.status, json.loads(response.read())
    finally:
        client.close()


def test_draft_survives_service_recreation_on_a_different_port(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    state = tmp_path / 'state'
    service = PersonalService(state)
    selected = service.add_project(project)['id']
    first = PersonalServer(service)
    thread = threading.Thread(target=first.serve_forever, daemon=True); thread.start()
    try:
        path = f'projects/{selected}/draft'
        assert request(first, path)[1] == {'project_id': selected, 'text': '', 'revision': 0}
        payload = {'project_id': selected, 'text': '下次修改后先运行相关测试。', 'revision': 0}
        assert request(first, path, token='wrong')[0] == 401
        assert request(first, 'draft', payload, token='wrong')[0] == 401
        assert request(first, 'draft', {**payload, 'unsupported': True})[0] == 400
        status, saved = request(first, 'draft', payload)
        assert status == 200 and saved['revision'] == 1
        status, conflict = request(first, 'draft', payload)
        assert status == 409 and payload['text'] not in json.dumps(conflict)
        assert request(first, path + '?unexpected=1')[0] == 400
        first.shutdown(); thread.join(timeout=5)
        assert not thread.is_alive()
        # Reserve the stopped listener until the replacement binds, guaranteeing
        # a changed origin rather than hoping the OS picks a different port.
        second = PersonalServer(PersonalService(state))
        worker = threading.Thread(target=second.serve_forever, daemon=True); worker.start()
        try:
            assert second.origin != first.origin
            assert request(second, path) == (200, saved)
            status, health = request(second, 'health')
            assert status == 200 and health['model_calls'] == health['harness_changes'] == 0
        finally:
            second.shutdown(); second.server_close(); worker.join(timeout=5)
            assert not worker.is_alive()
    finally:
        if thread.is_alive(): first.shutdown()
        first.server_close(); thread.join(timeout=5)


def test_project_isolation_empty_draft_and_no_learning_side_effects(tmp_path):
    store = EventStore(tmp_path / 'personal.sqlite3')
    one = store.add_project(tmp_path / 'one')['id']
    two = store.add_project(tmp_path / 'two')['id']
    saved = store.save_draft(one, 'Original local feedback', 0)
    assert store.draft(two)['revision'] == 0
    cleared = store.save_draft(one, '', saved['revision'])
    assert cleared['text'] == '' and cleared['revision'] == 2
    with store.connection() as db:
        for table in ('source_events', 'jobs', 'evolution_events'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


@pytest.mark.parametrize('text,revision', [('x' * (MAX_DRAFT_BYTES + 1), 0), ('字' * 6000, 0),
                                         (None, 0), ('text', -1), ('text', True)])
def test_invalid_draft_does_not_replace_preserved_text(tmp_path, text, revision):
    store = EventStore(tmp_path / 'personal.sqlite3')
    project = store.add_project(tmp_path / 'project')['id']
    saved = store.save_draft(project, 'Preserved text', 0)
    with pytest.raises(ValueError): store.save_draft(project, text, revision)
    assert store.draft(project) == saved
    with pytest.raises(ValueError, match='selected'): store.save_draft('unknown', 'text', 0)


@pytest.fixture(params=[1, 2])
def legacy(tmp_path, request):
    version = request.param
    path = tmp_path / 'personal.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript((ROOT / f'tests/fixtures/personal_v{version}.sql').read_text(encoding='utf-8'))
        db.execute("INSERT INTO projects VALUES ('p_one','synthetic-project')")
        db.execute("INSERT INTO sessions VALUES ('p_one','fixture','session')")
        db.execute("INSERT INTO source_events VALUES (1,'p_one','fixture','session','event','original body','original digest')")
        db.execute("INSERT INTO jobs VALUES ('j_old','p_one','demo','fixture','original job digest')")
        db.execute("INSERT INTO evolution_events VALUES ('j_old',1,'fixture.event','{}')")
        db.execute("INSERT INTO questions VALUES ('q_old','j_old','Original question','open',NULL)")
        if version == 2:
            db.execute("INSERT INTO jobs VALUES ('j_model','p_one','model','paused','model digest')")
            db.execute("INSERT INTO model_budgets(job_id,profile_id,config,limits,calls,fixture) "
                       "VALUES ('j_model','fixture','{}','{}',1,1)")
            db.execute("INSERT INTO model_calls VALUES ('j_model',1,'lease','plan','digest',7,'completed','{}',NULL)")
    return path, version


def test_migration_preserves_all_evidence_cursors_and_model_ledger(legacy):
    path, version = legacy
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f'SELECT * FROM {table}').fetchall()
                  for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    store = EventStore(path)
    with store.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        for table, rows in before.items():
            assert [tuple(row) for row in db.execute(f'SELECT * FROM {table}')] == rows
    assert store.models.totals()['jobs'] == version - 1
    assert store.save_draft('p_one', 'New local draft', 0)['revision'] == 1


@pytest.mark.parametrize('name', ['unrecognized', 'sqlitex_unrecognized'])
def test_unknown_schema_is_refused_without_changing_original_bytes(legacy, name):
    path, _ = legacy
    with sqlite3.connect(path) as db:
        db.execute(f'CREATE TABLE {name}(text TEXT)')
        db.execute(f"INSERT INTO {name} VALUES ('original user data')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='migration refused'): EventStore(path)
    assert path.read_bytes() == before


def test_failed_migration_rolls_back_job_rebuild_and_preserves_data(legacy, monkeypatch):
    import harnessgrad.personal.event_store as module
    path, version = legacy
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
    monkeypatch.setattr(module, 'DRAFTS_DDL', 'invalid SQL')
    with pytest.raises(sqlite3.Error): EventStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == version
        assert list(db.iterdump()) == before
