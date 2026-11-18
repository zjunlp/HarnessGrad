"""Stage A stays offline, project-scoped and independent of experiment execution."""
from __future__ import annotations

import copy
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from harnessgrad.personal.adapters.codex import CodexAdapter
from harnessgrad.personal.api import PersonalServer
from harnessgrad.personal.contracts import SourceEvent, decode, source_events
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.fixtures import example
from harnessgrad.personal.service import PersonalService

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def selected(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'keep.txt').write_text('original user data')
    service = PersonalService(tmp_path / 'state')
    row = service.add_project(project)
    return service, row, project


def test_preserves_sources_deduplicates_and_restarts_without_mutating_project(selected):
    service, project, root = selected
    original = {str(p): p.read_bytes() for p in root.iterdir()}
    value = example(); before = copy.deepcopy(value)
    first = service.import_session(project['id'], value)
    second = service.import_session(project['id'], value)
    assert first['added'] == 5 and second['added'] == 0 and second['duplicates'] == 5
    assert value == before
    reopened = PersonalService(service.root)
    rows = reopened.store.source_rows(project['id'])
    assert rows['events'][-1]['event_id'] == 'demo-4'
    assert rows['events'][-1]['truncated'] is False
    assert reopened.store.job_events(first['job_id'])[0]['type'] == 'import.completed'
    assert reopened.store.job_events(first['job_id'], 1) == []
    assert original == {str(p): p.read_bytes() for p in root.iterdir()}


def test_conflicting_source_rolls_back_the_entire_import(selected):
    service, project, _ = selected
    service.import_session(project['id'], example())
    changed = example()
    changed['events'][0]['event_id'] = 'brand-new'
    changed['events'][1]['text'] = 'changed provenance'
    with pytest.raises(ValueError, match='conflicts'):
        service.import_session(project['id'], changed)
    assert service.store.source_rows(project['id'])['total'] == 5


def test_projects_with_same_session_names_do_not_share_evidence(selected, tmp_path):
    service, first, _ = selected
    other = tmp_path / 'other'; other.mkdir()
    second = service.add_project(other)
    service.import_session(first['id'], example())
    assert service.store.source_rows(second['id'])['total'] == 0
    with pytest.raises(ValueError, match='not selected'):
        service.import_session('unknown', example())


def test_long_session_retains_tail_roles_and_incompleteness(selected):
    service, project, _ = selected
    base = example()['events'][0]
    rows = [{**base, 'event_id': f'event-{i}', 'text': f'event {i}'} for i in range(200)]
    rows[-1].update(role='tool', text='final result is missing', truncated=True)
    service.import_session(project['id'], {'schema': example()['schema'], 'events': rows})
    cursor = 0; collected = []
    for _ in range(4):
        page = service.store.source_rows(project['id'], after=cursor)
        collected.extend(page['events']); cursor = page['next_after']
    assert len(collected) == 200 and collected[-1]['truncated']
    assert collected[-1]['role'] == 'tool' and collected[-1]['event_id'] == 'event-199'


@pytest.mark.parametrize('change', [
    {'source': 'codex-private'}, {'role': 'system'}, {'truncated': 'false'},
    {'text': 'x' * 16385}, {'event_id': '../outside'}, {'timestamp': 'yesterday'},
])
def test_invalid_sources_fail_before_persistence(selected, change):
    service, project, _ = selected
    value = example(); value['events'][0].update(change)
    with pytest.raises(ValueError):
        service.import_session(project['id'], value)
    assert service.store.source_rows(project['id'])['total'] == 0


@pytest.mark.parametrize('raw', ['{"events":[],"events":[]}', '{"x":NaN}', '[]'])
def test_strict_json_reuses_public_contracts(raw):
    with pytest.raises(ValueError): decode(raw)


def test_unknown_database_version_does_not_rewrite_evidence(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE evidence (body TEXT)')
        db.execute("INSERT INTO evidence VALUES ('historical')")
        db.execute('PRAGMA user_version=99')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='migration required'): EventStore(path)
    assert path.read_bytes() == before


def test_failed_initial_migration_is_atomic(tmp_path, monkeypatch):
    import harnessgrad.personal.event_store as module
    path = tmp_path / 'new.sqlite3'
    monkeypatch.setattr(module, 'DDL', ('CREATE TABLE partial(id TEXT)', 'invalid sql'))
    with pytest.raises(sqlite3.Error): EventStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


@pytest.mark.parametrize('version,status', [('codex-cli 0.154.0', 'ready'), ('codex-cli 0.100.0', 'unavailable')])
def test_probe_does_not_infer_protocol_or_writes_from_version(version, status):
    result = CodexAdapter().probe(Path(sys.executable), read_version=lambda _: version)
    checks = {row.scope: row.status for row in result.checks}
    assert checks['codex.executable_version'] == status
    assert checks['codex.existing_session_observation'] == 'unchecked'
    assert checks['codex.harness_writes'] == 'refused'


def test_probe_missing_permission_and_managed_policy(tmp_path):
    assert CodexAdapter().probe(tmp_path/'missing').checks[0].status == 'unavailable'
    def denied(_): raise PermissionError('private path is not disclosed')
    value = CodexAdapter().probe(Path(sys.executable), managed_hooks=True, read_version=denied).as_dict()
    assert value['checks'][0]['status'] == 'refused'
    assert next(row for row in value['checks'] if row['scope'] == 'codex.hooks')['status'] == 'refused'
    assert 'private path' not in json.dumps(value)


def test_state_must_be_outside_checkout_and_selected_project(selected):
    service, _, project = selected
    with pytest.raises(ValueError, match='outside'): PersonalService(ROOT / 'uncreated-state')
    with pytest.raises(ValueError, match='overlap'): service.add_project(service.root)
    with pytest.raises(ValueError, match='overlap'): service.add_project(service.root.parent)
    assert (project/'keep.txt').read_text() == 'original user data'


@pytest.fixture
def server(selected):
    service, project, _ = selected
    server = PersonalServer(service)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield server, project
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        assert not thread.is_alive()


def request(server, path, value=None, *, authenticated=True, extra=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    headers = {'Content-Type': 'application/json'}
    if authenticated: headers['Authorization'] = 'Bearer ' + server.token
    headers.update(extra or {})
    try:
        client.request('GET' if value is None else 'POST', path,
                       body=None if value is None else json.dumps(value), headers=headers)
        response = client.getresponse()
        return response.status, response.read().decode('utf-8')
    finally: client.close()


def test_api_auth_origin_and_raw_duplicate_keys(server):
    host, project = server
    assert request(host, '/api/personal/health', authenticated=False)[0] == 401
    assert request(host, '/api/personal/health', extra={'Origin': 'http://other.local'})[0] == 403
    assert request(host, '/api/personal/health', extra={'Host': 'attacker.local'})[0] == 403
    status, body = request(host, '/api/personal/import', {'project_id': project['id'], 'raw_document': '{"a":1,"a":2}'})
    assert status == 400 and host.token not in body
    assert request(host, '/api/personal/projects/unknown/events')[0] == 400


def test_api_fixture_and_replay_never_claim_applied(server):
    host, project = server
    status, body = request(host, '/api/personal/import', {'project_id': project['id'], 'document': example()})
    assert status == 200 and json.loads(body)['added'] == 5
    for state in ('idle', 'input', 'working', 'question', 'done', 'details', 'failed'):
        status, body = request(host, '/api/personal/demo', {'project_id': project['id'], 'state': state})
        result = json.loads(body)
        assert status == 200 and result['fixture'] and not result['applied']
        status, events = request(host, '/api/personal/jobs/'+result['job_id']+'/events?after=0')
        assert status == 200 and json.loads(events)['events'][0]['type'] == 'demo.state_selected'
    status, health = request(host, '/api/personal/health')
    assert status == 200 and json.loads(health)['model_calls'] == 0 and json.loads(health)['harness_changes'] == 0
    assert request(host, '/api/personal/shutdown', {'instance': 'another-instance'})[0] == 400


def run_cli(tmp_path, *args):
    code = 'import sys;sys.path.insert(0,' + repr(str(ROOT)) + ');from harnessgrad.cli import main;raise SystemExit(main(sys.argv[1:]))'
    return subprocess.run([sys.executable, '-I', '-B', '-c', code, 'personal', *args],
                          cwd=tmp_path, capture_output=True, text=True, timeout=20)


def test_help_is_lazy_and_does_not_create_state(tmp_path):
    code = ('import sys;sys.path.insert(0,' + repr(str(ROOT)) + ');from harnessgrad.cli import main;'
            '\ntry: main(["personal","--help"])\nexcept SystemExit as exc: assert exc.code==0\n'
            'assert not any(k.startswith(("methods", "driver", "harnessgrad.personal.api")) for k in sys.modules)')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'serve' in result.stdout and 'project' in result.stdout and 'stop' in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_owned_service_lifecycle_survives_restart_and_refuses_other_identity(tmp_path):
    from harnessgrad.entrypoints.personal import connection, contact
    state = tmp_path/'state'; project = tmp_path/'project'; project.mkdir()
    assert run_cli(tmp_path, 'demo', '--state-root', str(state), '--project', str(project)).returncode == 0
    for _ in range(2):
        start = run_cli(tmp_path, 'serve', '--background', '--state-root', str(state))
        assert start.returncode == 0, start.stderr
        value = connection(state)
        try:
            assert value['token'] not in start.stdout + start.stderr
            assert contact(value)['projects']
            with pytest.raises(ValueError, match='identity'): contact({**value, 'instance': 'wrong'}, stop=True)
            assert contact(value)['instance'] == value['instance']
        finally:
            assert run_cli(tmp_path, 'stop', '--state-root', str(state)).returncode == 0
            import time
            for _ in range(50):
                if not (state/'connection.json').exists(): break
                time.sleep(.05)
            assert not (state/'connection.json').exists()


def test_public_static_assets_are_served_without_authentication(server):
    host, _ = server
    for path in ('/', '/personal.css', '/personal.js', '/personal-window.js', '/personal-draft.js', '/tokens.css'):
        status, text = request(host, path, authenticated=False)
        assert status == 200 and text


def test_concurrent_event_append_has_monotonic_persisted_cursors(selected):
    from concurrent.futures import ThreadPoolExecutor
    service, project, _ = selected
    job = service.store.demo(project['id'], 'idle')['job_id']
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda i: service.store.append_event(job, 'demo.viewed', {'index': i}), range(12)))
    assert sorted(r['seq'] for r in results) == list(range(2, 14))
    rows = service.store.job_events(job, 10)
    assert [r['seq'] for r in rows] == [11, 12, 13]
    assert len({r['payload']['index'] for r in service.store.job_events(job)[1:]}) == 12


def test_import_and_paging_limits_are_explicit(selected):
    service, project, _ = selected
    value = example(); value['events'] = [value['events'][0]] * 201
    with pytest.raises(ValueError, match='200'): service.import_session(project['id'], value)
    for after, limit in ((-1, 50), (0, 201), (False, 50)):
        with pytest.raises(ValueError, match='page'): service.store.source_rows(project['id'], after=after, limit=limit)
