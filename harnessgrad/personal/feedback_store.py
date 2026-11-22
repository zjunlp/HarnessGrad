"""Durable feedback and selected excerpts using the existing event-store transactions."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from harnessgrad.personal.contracts import decode, digest, encode, identifier
from harnessgrad.personal.evidence import MAX_EVIDENCE_BYTES, excerpt
from harnessgrad.personal.inputs import PersonalFeedback

FEEDBACK_DDL = (
    'CREATE TABLE feedback (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), '
    'idempotency_key TEXT NOT NULL, input_sha256 TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, '
    'created_at TEXT NOT NULL, UNIQUE(project_id,idempotency_key), UNIQUE(id,project_id))',
    'CREATE TABLE feedback_evidence (feedback_id TEXT NOT NULL, project_id TEXT NOT NULL, '
    'position INTEGER NOT NULL, source_instance TEXT NOT NULL, event_id TEXT NOT NULL, body TEXT NOT NULL, '
    'PRIMARY KEY(feedback_id,position), FOREIGN KEY(feedback_id,project_id) REFERENCES feedback(id,project_id), '
    'FOREIGN KEY(project_id,source_instance,event_id) REFERENCES source_events(project_id,source_instance,event_id))',
)
FEEDBACK_COLUMNS = {
    'feedback': ('id', 'project_id', 'idempotency_key', 'input_sha256', 'kind', 'text', 'created_at'),
    'feedback_evidence': ('feedback_id', 'project_id', 'position', 'source_instance', 'event_id', 'body'),
}


class FeedbackConflict(ValueError):
    """The same request key cannot acquire a different effect."""


class FeedbackStore:
    def __init__(self, store):
        self.store = store

    def submit(self, value: PersonalFeedback) -> dict:
        fingerprint = digest(value.as_dict())
        with self.store.connection() as db, self.store.transaction(db):
            self.store.require_project(db, value.project_id)
            prior = db.execute('SELECT * FROM feedback WHERE project_id=? AND idempotency_key=?',
                               (value.project_id, value.idempotency_key)).fetchone()
            if prior:
                if prior['input_sha256'] != fingerprint:
                    raise FeedbackConflict('feedback request key already has a different payload')
                return self.read(db, prior)
            if value.draft_revision is not None:
                draft = db.execute('SELECT text,revision FROM drafts WHERE project_id=?', (value.project_id,)).fetchone()
                if not draft or (draft['text'], draft['revision']) != (value.text, value.draft_revision):
                    raise FeedbackConflict('submitted draft is stale')
            selected = []
            for item in value.evidence:
                row = db.execute('SELECT body,sha256 FROM source_events WHERE project_id=? '
                                 'AND source_instance=? AND session_id=? AND event_id=?',
                                 (value.project_id, item.source_instance, item.session_id, item.event_id)).fetchone()
                if not row:
                    raise ValueError('evidence does not belong to the selected project and session')
                selected.append(excerpt(row, item))
            if sum(len(item['text'].encode('utf-8')) for item in selected) > MAX_EVIDENCE_BYTES:
                raise ValueError('selected evidence exceeds 128 KiB')
            feedback_id = 'f_' + uuid.uuid4().hex
            timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            db.execute('INSERT INTO feedback VALUES (?,?,?,?,?,?,?)',
                       (feedback_id, value.project_id, value.idempotency_key, fingerprint, value.kind, value.text, timestamp))
            for position, item in enumerate(selected):
                db.execute('INSERT INTO feedback_evidence VALUES (?,?,?,?,?,?)',
                           (feedback_id, value.project_id, position, item['source_instance'], item['event_id'], encode(item)))
            row = db.execute('SELECT * FROM feedback WHERE id=?', (feedback_id,)).fetchone()
            return self.read(db, row)

    @staticmethod
    def read(db, row) -> dict:
        evidence = [decode(item['body']) for item in db.execute(
            'SELECT body FROM feedback_evidence WHERE feedback_id=? ORDER BY position', (row['id'],))]
        return {**dict(row), 'schema': 'harnessgrad.personal.feedback.v1', 'origin': 'user',
                'status': 'recorded', 'evidence': evidence, 'evidence_count': len(evidence),
                'missing_context': not evidence or any(item['context_incomplete'] or item['partial'] for item in evidence)}

    def get(self, project_id: str, feedback_id: str) -> dict:
        identifier(feedback_id)
        with self.store.connection() as db:
            self.store.require_project(db, project_id)
            row = db.execute('SELECT * FROM feedback WHERE id=? AND project_id=?', (feedback_id, project_id)).fetchone()
            if not row:
                raise ValueError('unknown feedback for selected project')
            return self.read(db, row)

    def list(self, project_id: str, *, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('invalid feedback page')
        with self.store.connection() as db:
            self.store.require_project(db, project_id)
            return [dict(row) for row in db.execute(
                'SELECT id,kind,text,created_at,(SELECT count(*) FROM feedback_evidence e WHERE e.feedback_id=f.id) '
                'AS evidence_count FROM feedback f WHERE project_id=? ORDER BY rowid DESC LIMIT ?', (project_id, limit))]

    def count(self) -> int:
        with self.store.connection() as db:
            return db.execute('SELECT count(*) FROM feedback').fetchone()[0]
