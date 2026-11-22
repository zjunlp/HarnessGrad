"""D-owned SQLite state. Checkpoints and decisions share a transaction."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Callable

from harnessgrad.personal.contracts import decode, digest, encode, identifier
from harnessgrad.personal.learning.contracts import (
    DecisionReceipt,
    LearningPolicy,
    LearningPolicyUpdate,
    LearningScope,
    SourceItem,
    TriggerDecision,
)

APPLICATION_ID = 0x48474431  # HarnessGrad D1
SCHEMA_VERSION = 7
SCOPE = 'profile_id=? AND project_id=?'
SCOPE_COLUMNS = 'profile_id TEXT NOT NULL, project_id TEXT NOT NULL'
# Earlier reservations cannot prove the callback was never started. New ones insert NULL.
CALL_STARTED_COLUMN = 'call_started INTEGER DEFAULT 1 CHECK(call_started = 1)'
DDL = (
    f'CREATE TABLE policy_updates ({SCOPE_COLUMNS}, revision INTEGER NOT NULL, request_key TEXT NOT NULL, '
    'request_sha256 TEXT NOT NULL, confirmation_sha256 TEXT, body TEXT NOT NULL, '
    'PRIMARY KEY(profile_id,project_id,revision), UNIQUE(profile_id,project_id,request_key), '
    'UNIQUE(profile_id,project_id,confirmation_sha256))',
    f'CREATE TABLE checkpoints ({SCOPE_COLUMNS}, source_id TEXT NOT NULL, cursor TEXT NOT NULL, '
    'PRIMARY KEY(profile_id,project_id,source_id))',
    f'CREATE TABLE decisions (seq INTEGER PRIMARY KEY, {SCOPE_COLUMNS}, source_id TEXT NOT NULL, '
    'session_id TEXT NOT NULL, event_id TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL, '
    'reason TEXT NOT NULL, decided_at TEXT NOT NULL, evidence_ref TEXT, excerpt TEXT, '
    'UNIQUE(profile_id,project_id,source_id,session_id,event_id,sha256,status,reason))',
    'CREATE INDEX decisions_by_time ON decisions(profile_id,project_id,decided_at) '
    "WHERE status='triggered'",
    'CREATE INDEX decisions_by_scope ON decisions(profile_id,project_id,seq)',
    'CREATE INDEX decisions_by_evidence ON decisions(profile_id,project_id,evidence_ref)',
)
REFLECTION_DDL = (
    f'CREATE TABLE reflections ({SCOPE_COLUMNS}, id TEXT NOT NULL, input_sha256 TEXT NOT NULL, '
    f'draft TEXT, candidate TEXT, job_id TEXT, {CALL_STARTED_COLUMN}, PRIMARY KEY(profile_id,project_id,id))',
    f'CREATE TABLE reflection_evidence ({SCOPE_COLUMNS}, evidence_ref TEXT NOT NULL, reflection_id TEXT NOT NULL, '
    'PRIMARY KEY(profile_id,project_id,evidence_ref), FOREIGN KEY(profile_id,project_id,reflection_id) '
    'REFERENCES reflections(profile_id,project_id,id))',
)
MAINTENANCE_DDL = (
    f'CREATE TABLE learning_revisions ({SCOPE_COLUMNS}, item_id TEXT NOT NULL, revision INTEGER NOT NULL, '
    'body TEXT NOT NULL, PRIMARY KEY(profile_id,project_id,item_id,revision))',
    f'CREATE TABLE maintenance_decisions ({SCOPE_COLUMNS}, id TEXT NOT NULL, request_key TEXT NOT NULL, '
    'item_id TEXT NOT NULL, body TEXT NOT NULL, candidate TEXT, '
    'dispatch_sha256 TEXT, job_id TEXT, operation_id TEXT, PRIMARY KEY(profile_id,project_id,id), '
    'UNIQUE(profile_id,project_id,request_key), UNIQUE(profile_id,project_id,job_id), '
    'UNIQUE(profile_id,project_id,operation_id))',
    f'CREATE TABLE learning_operations ({SCOPE_COLUMNS}, job_id TEXT NOT NULL, operation_id TEXT NOT NULL, '
    'item_id TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(profile_id,project_id,job_id,operation_id), '
    'FOREIGN KEY(profile_id,project_id,item_id,revision) '
    'REFERENCES learning_revisions(profile_id,project_id,item_id,revision))',
)
TRIAL_DDL = (
    f'CREATE TABLE IF NOT EXISTS learning_trials ({SCOPE_COLUMNS}, id TEXT NOT NULL, request_key TEXT NOT NULL, '
    'request_sha256 TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(profile_id,project_id,id), '
    'UNIQUE(profile_id,project_id,request_key))'
)


class PolicyConflict(ValueError):
    """A stale revision or reused request key cannot acquire a different effect."""


class EvidenceUnavailable(PermissionError):
    """A selected excerpt is ineligible for new learning."""


class LearningStore:
    def __init__(self, path: Path):
        self.path = path
        with self.transaction() as db:
            identity = db.execute('PRAGMA application_id').fetchone()[0]
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if (identity, version) == (APPLICATION_ID, SCHEMA_VERSION):
                return
            if identity != APPLICATION_ID or not 1 <= version < SCHEMA_VERSION:
                if identity or version or db.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone():
                    raise ValueError('not a recognized learning database')
                for statement in DDL:
                    db.execute(statement)
                db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            if version < 2:
                for statement in REFLECTION_DDL:
                    db.execute(statement)
            elif version < 5:
                db.execute(f'ALTER TABLE reflections ADD COLUMN {CALL_STARTED_COLUMN}')
            if version < 3:
                for statement in MAINTENANCE_DDL:
                    db.execute(statement)
            # Older prototypes may already have trial receipts. Keep the original rows.
            db.execute(TRIAL_DDL)
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    def connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def transaction(self):
        with closing(self.connection()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _policy(scope: LearningScope, row: sqlite3.Row) -> LearningPolicy:
        body = decode(row['body'])
        body['source_ids'] = tuple(body['source_ids'])
        return LearningPolicy(scope, **body)

    @classmethod
    def policy(cls, db: sqlite3.Connection, scope: LearningScope) -> LearningPolicy:
        row = db.execute(
            f'SELECT body FROM policy_updates WHERE {SCOPE} ORDER BY revision DESC LIMIT 1',
            scope.key(),
        ).fetchone()
        return cls._policy(scope, row) if row else LearningPolicy(scope)

    def read_learning_policy(self, scope: LearningScope) -> LearningPolicy:
        with closing(self.connection()) as db:
            return self.policy(db, scope)

    def update_learning_policy(
        self, request: LearningPolicyUpdate, confirm: Callable[[LearningPolicyUpdate], bool] | None,
    ) -> LearningPolicy:
        scope = request.scope
        fingerprint = digest(asdict(request))
        with self.transaction() as db:
            prior = db.execute(
                f'SELECT * FROM policy_updates WHERE {SCOPE} AND request_key=?',
                (*scope.key(), request.idempotency_key),
            ).fetchone()
            if prior:
                if prior['request_sha256'] != fingerprint:
                    raise PolicyConflict('learning policy request key already has a different payload')
                return self._policy(scope, prior)
            current = self.policy(db, scope)
            if request.expected_revision != current.revision:
                raise PolicyConflict('learning policy revision has changed')
            new = request.next_policy()
            expands_access = ((new.enabled and not current.enabled)
                              or bool(set(new.source_ids) - set(current.source_ids)))
            confirmation_sha256 = None
            if expands_access:
                if request.confirmation_ref is None or confirm is None or not confirm(request):
                    raise PermissionError('learning source selection requires explicit confirmation')
                confirmation_sha256 = digest(request.confirmation_ref)
                used = db.execute(
                    f'SELECT 1 FROM policy_updates WHERE {SCOPE} AND confirmation_sha256=?',
                    (*scope.key(), confirmation_sha256),
                ).fetchone()
                if used:
                    raise PermissionError('learning source confirmation was already used')
            body = asdict(new)
            body.pop('scope')
            db.execute('INSERT INTO policy_updates VALUES (?,?,?,?,?,?,?)',
                       (*scope.key(), new.revision, request.idempotency_key, fingerprint,
                        confirmation_sha256, encode(body)))
            return new

    @staticmethod
    def checkpoint(db: sqlite3.Connection, scope: LearningScope, source_id: str) -> str | None:
        row = db.execute(
            f'SELECT cursor FROM checkpoints WHERE {SCOPE} AND source_id=?', (*scope.key(), source_id),
        ).fetchone()
        return row['cursor'] if row else None

    @staticmethod
    def save_checkpoint(db: sqlite3.Connection, scope: LearningScope, source_id: str, cursor: str):
        db.execute(
            'INSERT INTO checkpoints VALUES (?,?,?,?) ON CONFLICT(profile_id,project_id,source_id) '
            'DO UPDATE SET cursor=excluded.cursor', (*scope.key(), source_id, cursor),
        )

    @staticmethod
    def trigger_history(
        db: sqlite3.Connection, scope: LearningScope, day_start: str,
    ) -> tuple[int, datetime | None]:
        used = db.execute(
            f"SELECT count(*) FROM decisions WHERE {SCOPE} AND status='triggered' "
            'AND decided_at BETWEEN ? AND ?',
            (*scope.key(), day_start, day_start + 'T23:59:59.999999+00:00'),
        ).fetchone()[0]
        last = db.execute(
            f"SELECT max(decided_at) FROM decisions WHERE {SCOPE} AND status='triggered'",
            scope.key(),
        ).fetchone()[0]
        return used, datetime.fromisoformat(last) if last is not None else None

    @staticmethod
    def receipt(row: sqlite3.Row) -> DecisionReceipt:
        ref = row['evidence_ref']
        refs = (ref,) if ref and row['status'] != 'skipped' and row['reason'] != 'source_conflict' else ()
        return DecisionReceipt(
            row['seq'], row['source_id'], row['session_id'], row['event_id'], row['sha256'],
            TriggerDecision(row['status'], refs, row['reason'] or None), ref,
        )

    @classmethod
    def original(
        cls, db: sqlite3.Connection, scope: LearningScope, item: SourceItem,
    ) -> DecisionReceipt | None:
        # Prefer the latest settled decision for the original event over deferrals
        # or conflicting deliveries. A classification can settle a prior skip.
        row = db.execute(
            f'SELECT * FROM decisions WHERE {SCOPE} AND source_id=? AND session_id=? AND event_id=? '
            "ORDER BY reason='source_conflict',status='deferred',seq DESC LIMIT 1",
            (*scope.key(), item.source_id, item.session_id, item.event_id),
        ).fetchone()
        return cls.receipt(row) if row else None

    @staticmethod
    def record(
        db: sqlite3.Connection, scope: LearningScope, item: SourceItem, decision: TriggerDecision,
        ref: str | None, excerpt: dict | None, now: datetime,
    ) -> DecisionReceipt | None:
        cursor = db.execute(
            'INSERT INTO decisions(profile_id,project_id,source_id,session_id,event_id,sha256,status,reason,'
            'decided_at,evidence_ref,excerpt) VALUES (?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(profile_id,project_id,source_id,session_id,event_id,sha256,status,reason) DO NOTHING',
            (*scope.key(), item.source_id, item.session_id, item.event_id, item.event_sha256,
             decision.status, decision.reason_code or '', now.isoformat(timespec='microseconds'),
             ref, encode(excerpt) if excerpt is not None else None),
        )
        if cursor.rowcount == 0:
            return None
        return DecisionReceipt(cursor.lastrowid, item.source_id, item.session_id, item.event_id,
                               item.event_sha256, decision, ref)

    def read_receipts(
        self, scope: LearningScope, *, after: int = 0, limit: int = 50,
    ) -> tuple[DecisionReceipt, ...]:
        if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('invalid learning receipt window')
        with closing(self.connection()) as db:
            rows = db.execute(
                f'SELECT * FROM decisions WHERE {SCOPE} AND seq>? ORDER BY seq LIMIT ?',
                (*scope.key(), after, limit),
            )
            return tuple(self.receipt(row) for row in rows)

    def read_evidence(self, scope: LearningScope, ref: str) -> dict:
        identifier(ref)
        with closing(self.connection()) as db:
            row = db.execute(
                f'SELECT source_id,excerpt FROM decisions WHERE {SCOPE} AND evidence_ref=? ORDER BY seq LIMIT 1',
                (*scope.key(), ref),
            ).fetchone()
            policy = self.policy(db, scope)
            if not row or not policy.enabled or row['source_id'] not in policy.source_ids:
                raise PermissionError('evidence is outside the enabled learning sources')
            return decode(row['excerpt'])

    def read_reflection_evidence(self, scope: LearningScope, refs: tuple[str, ...]) -> tuple[dict, ...]:
        """Return only scoped, authorized excerpts with a final D1 trigger."""
        with closing(self.connection()) as db:
            db.execute('BEGIN')
            return self.reflection_excerpts(db, scope, refs)

    @classmethod
    def reflection_excerpts(
        cls, db: sqlite3.Connection, scope: LearningScope, refs: tuple[str, ...],
    ) -> tuple[dict, ...]:
        policy = cls.policy(db, scope)
        if not policy.enabled:
            raise EvidenceUnavailable('learning_disabled')
        selected = []
        for ref in refs:
            identifier(ref)
            row = db.execute(
                f"SELECT * FROM decisions WHERE {SCOPE} AND evidence_ref=? AND status='triggered'",
                (*scope.key(), ref),
            ).fetchone()
            if row is None:
                raise EvidenceUnavailable('evidence_not_triggered')
            if row['source_id'] not in policy.source_ids:
                raise EvidenceUnavailable('source_revoked')
            conflict = db.execute(
                f"SELECT 1 FROM decisions WHERE {SCOPE} AND source_id=? AND session_id=? AND event_id=? "
                "AND reason='source_conflict' LIMIT 1",
                (*scope.key(), row['source_id'], row['session_id'], row['event_id']),
            ).fetchone()
            if conflict:
                raise EvidenceUnavailable('source_conflict')
            selected.append({'ref': ref, **decode(row['excerpt'])})
        return tuple(selected)
