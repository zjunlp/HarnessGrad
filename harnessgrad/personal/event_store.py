"""Personal records and transactional model-budget, draft and feedback migrations."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import uuid

from harnessgrad.personal.contracts import MAX_DRAFT_BYTES, SourceEvent, decode, digest, encode, identifier
from harnessgrad.personal.model_store import MODEL_DDL, ModelStore
from harnessgrad.personal.evidence import EvidenceStore, INDEX_TABLES, source_text
from harnessgrad.personal.feedback_store import FEEDBACK_COLUMNS, FEEDBACK_DDL, FeedbackStore

from harnessgrad.personal.proposal_run_store import (
    PROPOSAL_EVENT_COLUMNS, PROPOSAL_EVENT_DDL, PROPOSAL_RUN_COLUMNS, PROPOSAL_RUN_DDL, ProposalRunStore,
)

SCHEMA_VERSION = 6
DRAFTS_DDL = ('CREATE TABLE drafts (project_id TEXT PRIMARY KEY REFERENCES projects(id), '
              'text TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0))')
JOBS_DDL = ('CREATE TABLE jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), '
            "kind TEXT NOT NULL CHECK(kind IN ('import','demo','model')), status TEXT NOT NULL "
            "CHECK(status IN ('completed','fixture','ready','running','paused','failed')), input_sha256 TEXT NOT NULL)")
DDL = (
    'CREATE TABLE projects (id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE)',
    'CREATE TABLE sessions (project_id TEXT NOT NULL REFERENCES projects(id), '
    'source_instance TEXT NOT NULL, id TEXT NOT NULL, '
    'PRIMARY KEY(project_id,source_instance,id))',
    'CREATE TABLE source_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, '
    'project_id TEXT NOT NULL, source_instance TEXT NOT NULL, session_id TEXT NOT NULL, '
    'event_id TEXT NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL, '
    'UNIQUE(project_id,source_instance,event_id), '
    'FOREIGN KEY(project_id,source_instance,session_id) REFERENCES sessions(project_id,source_instance,id))',
    JOBS_DDL,
    'CREATE TABLE evolution_events (job_id TEXT NOT NULL REFERENCES jobs(id), '
    'seq INTEGER NOT NULL CHECK(seq>0), type TEXT NOT NULL, payload TEXT NOT NULL, '
    'PRIMARY KEY(job_id,seq))',
    'CREATE TABLE questions (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), '
    "prompt TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','answered')), response TEXT)",
) + MODEL_DDL + (DRAFTS_DDL,) + FEEDBACK_DDL + (PROPOSAL_RUN_DDL, PROPOSAL_EVENT_DDL)

V1_COLUMNS = {
    'projects': ('id', 'root'), 'sessions': ('project_id', 'source_instance', 'id'),
    'source_events': ('seq', 'project_id', 'source_instance', 'session_id', 'event_id', 'body', 'sha256'),
    'jobs': ('id', 'project_id', 'kind', 'status', 'input_sha256'),
    'evolution_events': ('job_id', 'seq', 'type', 'payload'),
    'questions': ('id', 'job_id', 'prompt', 'status', 'response'),
}
V2_COLUMNS = {**V1_COLUMNS,
    'model_budgets': ('job_id', 'profile_id', 'config', 'limits', 'calls', 'revisions',
                      'elapsed', 'active_since', 'lease', 'recoveries', 'fixture'),
    'model_calls': ('job_id', 'seq', 'lease', 'stage', 'input_sha256', 'input_bytes',
                   'status', 'usage', 'failure_kind'),
}
V3_COLUMNS = {**V2_COLUMNS, 'drafts': ('project_id', 'text', 'revision')}
V4_COLUMNS = {**V3_COLUMNS, **FEEDBACK_COLUMNS}
V5_COLUMNS = {**V4_COLUMNS, 'proposal_runs': PROPOSAL_RUN_COLUMNS[:-1]}
V6_COLUMNS = {**V4_COLUMNS, 'proposal_runs': PROPOSAL_RUN_COLUMNS, 'proposal_events': PROPOSAL_EVENT_COLUMNS}


class DraftConflict(ValueError):
    """A stale editor must retain its text instead of overwriting another draft."""


class EventStore:
    def __init__(self, path: Path):
        self.path = path
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, SCHEMA_VERSION):
                raise ValueError('unsupported personal database version; migration required')
            if version == 0:
                with self.transaction(db):
                    if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                        raise ValueError('refusing to migrate an unknown database')
                    for statement in DDL:
                        db.execute(statement)
                    db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            elif version < SCHEMA_VERSION:
                self.migrate(db, version)
            else:
                self.require_schema(db, V6_COLUMNS, version, extras=INDEX_TABLES)
            for table in V6_COLUMNS:
                db.execute(f'SELECT * FROM {table} LIMIT 0')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('personal database contains invalid references')
            db.execute('PRAGMA journal_mode=WAL')
        self.models = ModelStore(self)
        self.feedback = FeedbackStore(self)
        self.evidence = EvidenceStore(self)
        self.proposal_runs = ProposalRunStore(self)

    @staticmethod
    def require_schema(db, columns_by_table, version, *, extras=()):
        objects = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
        if any(row['type'] != 'table' or row['name'] not in {*columns_by_table, *extras} for row in objects):
            raise ValueError(f'unrecognized v{version} schema objects; migration refused')
        for table, columns in columns_by_table.items():
            actual = tuple(row['name'] for row in db.execute(f'PRAGMA table_info({table})'))
            if actual != columns:
                raise ValueError(f'unrecognized v{version} schema; migration refused')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError(f'invalid v{version} references; migration refused')

    def migrate(self, db, version):
        columns = {1: V1_COLUMNS, 2: V2_COLUMNS, 3: V3_COLUMNS, 4: V4_COLUMNS, 5: V5_COLUMNS}[version]
        self.require_schema(db, columns, version, extras=INDEX_TABLES if version >= 4 else ())
        # Rebuild only the job constraints. Child evidence and event tables stay in place.
        if version == 1:
            db.execute('PRAGMA foreign_keys=OFF')
        try:
            with self.transaction(db):
                if version == 1:
                    db.execute(JOBS_DDL.replace('CREATE TABLE jobs ', 'CREATE TABLE jobs_v2 ', 1))
                    db.execute('INSERT INTO jobs_v2 SELECT id,project_id,kind,status,input_sha256 FROM jobs')
                    db.execute('DROP TABLE jobs')
                    db.execute('ALTER TABLE jobs_v2 RENAME TO jobs')
                    for statement in MODEL_DDL:
                        db.execute(statement)
                if version < 3:
                    db.execute(DRAFTS_DDL)
                if version < 4:
                    for statement in FEEDBACK_DDL:
                        db.execute(statement)
                if version == 5:
                    db.execute('ALTER TABLE proposal_runs RENAME TO proposal_runs_v5')
                db.execute(PROPOSAL_RUN_DDL)
                db.execute(PROPOSAL_EVENT_DDL)
                if version == 5:
                    db.execute('INSERT INTO proposal_runs SELECT *,NULL FROM proposal_runs_v5')
                    db.execute('INSERT INTO proposal_events SELECT feedback_id,1,status FROM proposal_runs')
                    db.execute('DROP TABLE proposal_runs_v5')
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('personal migration changed references')
                db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        finally:
            db.execute('PRAGMA foreign_keys=ON')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.create_function('personal_source_text', 1, source_text, deterministic=True)
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    @contextmanager
    def transaction(db):
        db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            db.rollback()
            raise
        else:
            db.commit()

    def add_project(self, root: Path) -> dict:
        project_id = 'p_' + digest(str(root))[:24]
        with self.connection() as db, self.transaction(db):
            db.execute('INSERT OR IGNORE INTO projects VALUES (?,?)', (project_id, str(root)))
        return {'id': project_id, 'root': str(root)}

    def project_root(self, project_id: str) -> Path:
        identifier(project_id)
        with self.connection() as db:
            row = db.execute('SELECT root FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise ValueError('project was not selected')
        return Path(row['root'])

    def projects(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM projects ORDER BY root')]

    @staticmethod
    def require_project(db, project_id):
        identifier(project_id)
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
            raise ValueError('project was not selected')

    def draft(self, project_id: str) -> dict:
        with self.connection() as db:
            self.require_project(db, project_id)
            row = db.execute('SELECT text,revision FROM drafts WHERE project_id=?', (project_id,)).fetchone()
        return {'project_id': project_id, 'text': row['text'] if row else '',
                'revision': row['revision'] if row else 0}

    def save_draft(self, project_id: str, text: str, revision: int) -> dict:
        if (not isinstance(text, str) or len(text.encode('utf-8')) > MAX_DRAFT_BYTES
                or type(revision) is not int or revision < 0):
            raise ValueError('draft must contain at most 16 KiB and a nonnegative revision')
        with self.connection() as db, self.transaction(db):
            self.require_project(db, project_id)
            row = db.execute('SELECT revision FROM drafts WHERE project_id=?', (project_id,)).fetchone()
            if revision != (row['revision'] if row else 0):
                raise DraftConflict('draft changed in another editor')
            db.execute('INSERT INTO drafts VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE '
                       'SET text=excluded.text,revision=excluded.revision', (project_id, text, revision + 1))
        return {'project_id': project_id, 'text': text, 'revision': revision + 1}

    def import_events(self, project_id: str, events: tuple[SourceEvent, ...]) -> dict:
        job_id = 'j_' + uuid.uuid4().hex
        added = 0
        with self.connection() as db, self.transaction(db):
            self.require_project(db, project_id)
            for event in events:
                row = event.as_dict()
                body, fingerprint = encode(row), digest(row)
                prior = db.execute('SELECT sha256 FROM source_events WHERE project_id=? '
                                   'AND source_instance=? AND event_id=?',
                                   (project_id, event.source_instance, event.event_id)).fetchone()
                if prior:
                    if prior['sha256'] != fingerprint:
                        raise ValueError('source event ID conflicts with preserved evidence')
                    continue
                db.execute('INSERT OR IGNORE INTO sessions VALUES (?,?,?)',
                           (project_id, event.source_instance, event.session_id))
                inserted = db.execute('INSERT INTO source_events(project_id,source_instance,session_id,event_id,body,sha256) '
                           'VALUES (?,?,?,?,?,?)', (project_id, event.source_instance, event.session_id,
                                                   event.event_id, body, fingerprint))
                self.evidence.index(db, inserted.lastrowid, event.text)
                added += 1
            result = {'job_id': job_id, 'added': added, 'duplicates': len(events) - added,
                      'source': 'synthetic', 'status': 'completed'}
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',
                       (job_id, project_id, 'import', 'completed', digest([e.as_dict() for e in events])))
            db.execute('INSERT INTO evolution_events VALUES (?,?,?,?)',
                       (job_id, 1, 'import.completed', encode(result)))
        return result

    def sessions(self, project_id: str) -> list[dict]:
        with self.connection() as db:
            self.require_project(db, project_id)
            return [dict(row) for row in db.execute(
                'SELECT source_instance,session_id,count(*) AS event_count FROM source_events '
                'WHERE project_id=? GROUP BY source_instance,session_id ORDER BY min(seq)', (project_id,))]

    def source_rows(self, project_id: str, *, after: int = 0, limit: int = 50) -> dict:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('invalid source page')
        with self.connection() as db:
            self.require_project(db, project_id)
            rows = db.execute('SELECT seq,body FROM source_events WHERE project_id=? AND seq>? '
                              'ORDER BY seq LIMIT ?', (project_id, after, limit)).fetchall()
            total = db.execute('SELECT count(*) FROM source_events WHERE project_id=?', (project_id,)).fetchone()[0]
        return {'events': [{'seq': r['seq'], **decode(r['body'])} for r in rows],
                'total': total, 'next_after': rows[-1]['seq'] if rows else after}

    def demo(self, project_id: str, state: str) -> dict:
        if state not in ('idle', 'input', 'working', 'question', 'done', 'details', 'failed'):
            raise ValueError('unknown fixture state')
        job_id = 'j_' + uuid.uuid4().hex
        payload = {'state': state, 'fixture': True, 'applied': False,
                   'detail': 'Stage A UI fixture; no model, proposal or harness change.'}
        with self.connection() as db, self.transaction(db):
            self.require_project(db, project_id)
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (job_id, project_id, 'demo', 'fixture', digest(payload)))
            db.execute('INSERT INTO evolution_events VALUES (?,?,?,?)', (job_id, 1, 'demo.state_selected', encode(payload)))
            if state == 'question':
                db.execute('INSERT INTO questions VALUES (?,?,?,?,?)',
                           ('q_' + uuid.uuid4().hex, job_id, '示例：请选择要关联的会话。', 'open', None))
        return {'job_id': job_id, **payload}

    def job_events(self, job_id: str, after: int = 0) -> list[dict]:
        identifier(job_id)
        if type(after) is not int or after < 0:
            raise ValueError('invalid event cursor')
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
                raise ValueError('unknown job')
            return [{'job_id': job_id, 'seq': r['seq'], 'type': r['type'], 'payload': decode(r['payload'])}
                    for r in db.execute('SELECT * FROM evolution_events WHERE job_id=? AND seq>? '
                                        'ORDER BY seq LIMIT 200', (job_id, after))]

    def append_event(self, job_id: str, kind: str, payload: dict) -> dict:
        identifier(job_id)
        identifier(kind)
        body = encode(decode(encode(payload)))
        with self.connection() as db, self.transaction(db):
            if not db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
                raise ValueError('unknown job')
            return self.add_event(db, job_id, kind, decode(body))

    @staticmethod
    def add_event(db, job_id: str, kind: str, payload: dict) -> dict:
        """Append inside an existing caller-owned transaction, including reservations."""
        identifier(job_id)
        identifier(kind)
        body = encode(decode(encode(payload)))
        seq = db.execute('SELECT coalesce(max(seq),0)+1 FROM evolution_events WHERE job_id=?',
                         (job_id,)).fetchone()[0]
        db.execute('INSERT INTO evolution_events VALUES (?,?,?,?)', (job_id, seq, kind, body))
        return {'job_id': job_id, 'seq': seq, 'type': kind, 'payload': decode(body)}
