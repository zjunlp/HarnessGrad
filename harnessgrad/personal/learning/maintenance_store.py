"""Append-only learning revisions and scoped maintenance receipts."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, replace
import sqlite3

from harnessgrad.personal.contracts import MAX_BYTES, decode, digest, encode
from harnessgrad.personal.execution_lock import execution_lock
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.maintenance_contracts import (
    CurrentVersion, ItemRevision, LearningItem, MaintenanceDecision, MaintenanceRequest,
    OperationRef, VersionEntry, VersionReceipt,
)
from harnessgrad.personal.learning.reflection_contracts import CandidateRequest, JobRef, SnapshotRef
from harnessgrad.personal.learning.store import LearningStore, SCOPE


@dataclass(frozen=True)
class Revision:
    item: LearningItem
    candidate: CandidateRequest
    path: str
    topic_id: str
    criterion: str
    versions: tuple[CurrentVersion, ...] = ()
    receipt: VersionReceipt | None = None
    merged_into: str | None = None
    rolled_back: bool = False
    versions_verified: bool = False
    bound_version_ref: str | None = None

    @classmethod
    def from_json(cls, raw: str) -> Revision:
        body = decode(raw)
        item = body['item']
        for field in ('target_ids', 'evidence_refs', 'conditions'):
            item[field] = tuple(item[field])
        item['job'] = JobRef(**item['job']) if item['job'] else None
        body['item'] = LearningItem(**item)
        body['candidate'] = CandidateRequest.from_dict(body['candidate'])
        body['versions'] = tuple(CurrentVersion(**value) for value in body['versions'])
        if receipt := body['receipt']:
            receipt['operation'] = OperationRef.from_dict(receipt['operation'])
            receipt['entries'] = tuple(VersionEntry(**value) for value in receipt['entries'])
            body['receipt'] = VersionReceipt(**receipt)
        return cls(**body)

    def next(self, **changes) -> Revision:
        return replace(self, item=replace(self.item, revision=self.item.revision + 1, **changes))


@dataclass(frozen=True)
class CandidateDraft:
    snapshot: SnapshotRef
    files: dict[str, str]


@dataclass(frozen=True)
class MaintenanceRecord:
    request: MaintenanceRequest
    decision: MaintenanceDecision
    revision: int
    draft: CandidateDraft | None = None
    rollback: OperationRef | None = None
    dispatch_sha256: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> MaintenanceRecord:
        body = decode(row['body'])
        request = body['request']
        request['scope'] = LearningScope(**request['scope'])
        request['items'] = tuple(ItemRevision(**item) for item in request['items'])
        request = MaintenanceRequest(**request)
        decision = body['decision']
        if row['candidate']:
            decision['candidate'] = CandidateRequest.from_dict(decode(row['candidate']))
        if row['job_id']:
            decision['job'] = JobRef(*request.scope.key(), row['job_id'])
        elif decision['job']:
            decision['job'] = JobRef(**decision['job'])
        decision = MaintenanceDecision(**decision)
        draft = body['draft']
        if draft:
            selected = draft['snapshot']
            selected['target_ids'] = tuple(selected['target_ids'])
            draft = CandidateDraft(SnapshotRef(**selected), draft['files'])
        rollback = OperationRef(decision.job, row['operation_id']) if row['operation_id'] else None
        return cls(request, decision, body['revision'], draft, rollback, row['dispatch_sha256'])


class MaintenanceStore:
    def __init__(self, store: LearningStore):
        self.store = store

    def lock(self, scope: LearningScope):
        database = self.store.path.resolve()
        key = digest([database.name, *scope.key()])
        return execution_lock(database.with_name(f'maintenance-{key}.lock'), kind='maintenance')

    def head(self, scope: LearningScope, item_id: str) -> Revision | None:
        with closing(self.store.connection()) as db:
            row = db.execute(
                f'SELECT body FROM learning_revisions WHERE {SCOPE} AND item_id=? ORDER BY revision DESC LIMIT 1',
                (*scope.key(), item_id),
            ).fetchone()
        return Revision.from_json(row['body']) if row else None

    def revision(self, scope: LearningScope, item_id: str, revision: int) -> Revision | None:
        with closing(self.store.connection()) as db:
            row = db.execute(f'SELECT body FROM learning_revisions WHERE {SCOPE} AND item_id=? AND revision=?',
                             (*scope.key(), item_id, revision)).fetchone()
        return Revision.from_json(row['body']) if row else None

    def heads(self, scope: LearningScope, *, after: str = '', limit: int = -1) -> tuple[Revision, ...]:
        with closing(self.store.connection()) as db:
            rows = db.execute(
                'SELECT r.body FROM learning_revisions r WHERE r.profile_id=? AND r.project_id=? '
                'AND r.item_id>? AND NOT EXISTS (SELECT 1 FROM learning_revisions newer '
                'WHERE newer.profile_id=r.profile_id AND newer.project_id=r.project_id '
                'AND newer.item_id=r.item_id AND newer.revision>r.revision) ORDER BY r.item_id LIMIT ?',
                (*scope.key(), after, limit),
            )
            return tuple(Revision.from_json(row['body']) for row in rows)

    def history(self, scope: LearningScope, item_id: str) -> tuple[Revision, ...]:
        with closing(self.store.connection()) as db:
            rows = db.execute(f'SELECT body FROM learning_revisions WHERE {SCOPE} AND item_id=? ORDER BY revision',
                              (*scope.key(), item_id))
            return tuple(Revision.from_json(row['body']) for row in rows)

    @staticmethod
    def _append(db: sqlite3.Connection, scope: LearningScope, value: Revision) -> None:
        db.execute('INSERT INTO learning_revisions VALUES (?,?,?,?,?)',
                   (*scope.key(), value.item.item_id, value.item.revision, encode(asdict(value))))

    def append(
        self, scope: LearningScope, values: tuple[Revision, ...], operation: OperationRef | None = None,
    ) -> None:
        if not values:
            return
        with self.store.transaction() as db:
            for value in values:
                self._append(db, scope, value)
            if operation is not None:
                subject = values[0]
                db.execute('INSERT INTO learning_operations VALUES (?,?,?,?,?,?)',
                           (*scope.key(), operation.job.job_id, operation.operation_id,
                            subject.item.item_id, subject.item.revision))

    def operation_revision(self, scope: LearningScope, operation: OperationRef) -> Revision | None:
        with closing(self.store.connection()) as db:
            row = db.execute(
                'SELECT r.body FROM learning_operations o JOIN learning_revisions r '
                'USING (profile_id,project_id,item_id,revision) '
                'WHERE o.profile_id=? AND o.project_id=? AND o.job_id=? AND o.operation_id=?',
                (*scope.key(), operation.job.job_id, operation.operation_id),
            ).fetchone()
        return Revision.from_json(row['body']) if row else None

    def action(self, scope: LearningScope, field: str, value: str) -> MaintenanceRecord | None:
        if field not in ('id', 'request_key', 'job_id', 'operation_id'):
            raise ValueError('unknown maintenance lookup')
        with closing(self.store.connection()) as db:
            row = db.execute(f'SELECT * FROM maintenance_decisions WHERE {SCOPE} AND {field}=?',
                             (*scope.key(), value)).fetchone()
        return MaintenanceRecord.from_row(row) if row else None

    def rollbacks(self, scope: LearningScope, item_id: str) -> tuple[MaintenanceRecord, ...]:
        with closing(self.store.connection()) as db:
            rows = db.execute(f'SELECT * FROM maintenance_decisions WHERE {SCOPE} AND item_id=? '
                              'AND dispatch_sha256 IS NOT NULL AND job_id IS NULL', (*scope.key(), item_id))
            actions = tuple(MaintenanceRecord.from_row(row) for row in rows)
        return tuple(record for record in actions if record.decision.status == 'needs_rollback')

    def save_action(self, record: MaintenanceRecord, updates: tuple[Revision, ...] = ()) -> None:
        request = record.request
        body = encode(asdict(record))
        if len(body.encode('utf-8')) > MAX_BYTES:
            raise ValueError('maintenance decision exceeds the record limit')
        with self.store.transaction() as db:
            for item in updates:
                self._append(db, request.scope, item)
            db.execute('INSERT INTO maintenance_decisions(profile_id,project_id,id,request_key,item_id,body) '
                       'VALUES (?,?,?,?,?,?)',
                       (*request.scope.key(), record.decision.decision_id, request.idempotency_key,
                        request.items[0].item_id, body))

    def record_result(self, record: MaintenanceRecord, field: str, value: str) -> None:
        if field not in ('candidate', 'dispatch_sha256', 'job_id', 'operation_id'):
            raise ValueError('unknown maintenance result')
        with self.store.transaction() as db:
            updated = db.execute(
                f'UPDATE maintenance_decisions SET {field}=? WHERE {SCOPE} AND id=? AND {field} IS NULL',
                (value, *record.request.scope.key(), record.decision.decision_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError('maintenance result is already recorded or has no decision')

    def clear_rejected_rollback(self, record: MaintenanceRecord) -> None:
        with self.store.transaction() as db:
            db.execute(f'UPDATE maintenance_decisions SET dispatch_sha256=NULL WHERE {SCOPE} AND id=? '
                       'AND operation_id IS NULL', (*record.request.scope.key(), record.decision.decision_id))
