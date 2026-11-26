"""Durable D2 reservations, call boundaries, decisions and queue acknowledgements."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
import sqlite3

from harnessgrad.personal.contracts import decode, digest, encode
from harnessgrad.personal.execution_lock import execution_lock
from harnessgrad.personal.learning.reflection_contracts import (
    CandidateRequest,
    Experience,
    JobRef,
    LearningStatus,
    ReflectionInput,
)
from harnessgrad.personal.learning.store import LearningStore, SCOPE


class ReflectionConflict(ValueError):
    """Previously reserved evidence cannot acquire a different reflection input."""


@dataclass(frozen=True)
class ReflectionDraft:
    status: LearningStatus
    reason_code: str | None = None
    experience: Experience | None = None
    conditions: tuple[str, ...] = ()
    files: dict[str, str] | None = None

    @classmethod
    def from_json(cls, raw: str) -> ReflectionDraft:
        body = decode(raw)
        if body['experience'] is not None:
            body['experience'] = Experience(**body['experience'])
        body['conditions'] = tuple(body['conditions'])
        return cls(**body)


@dataclass(frozen=True)
class ReflectionRecord:
    draft: ReflectionDraft | None = None
    candidate: CandidateRequest | None = None
    job: JobRef | None = None
    call_started: bool = False


class ReflectionStore:
    def __init__(self, store: LearningStore):
        self.store = store

    @staticmethod
    def key(request: ReflectionInput) -> str:
        return 'd2_' + digest([*request.scope.key(), request.evidence_refs])

    def lock(self, request: ReflectionInput):
        database = self.store.path.resolve()
        lock_id = digest([database.name, self.key(request)])
        return execution_lock(database.with_name(f'reflection-{lock_id}.lock'), kind='reflection')

    def _row(self, db: sqlite3.Connection, request: ReflectionInput) -> sqlite3.Row | None:
        row = db.execute(
            f'SELECT * FROM reflections WHERE {SCOPE} AND id=?', (*request.scope.key(), self.key(request)),
        ).fetchone()
        if row and row['input_sha256'] != digest(asdict(request)):
            raise ReflectionConflict('reflection input differs for the same evidence')
        return row

    def read(self, request: ReflectionInput) -> ReflectionRecord | None:
        with closing(self.store.connection()) as db:
            row = self._row(db, request)
        if row is None:
            return None
        return ReflectionRecord(
            ReflectionDraft.from_json(row['draft']) if row['draft'] is not None else None,
            CandidateRequest.from_dict(decode(row['candidate'])) if row['candidate'] is not None else None,
            JobRef(*request.scope.key(), row['job_id']) if row['job_id'] is not None else None,
            call_started=row['call_started'] is not None,
        )

    def claim(self, request: ReflectionInput) -> tuple[dict, ...]:
        scope = request.scope.key()
        key = self.key(request)
        with self.store.transaction() as db:
            evidence = self.store.reflection_excerpts(db, request.scope, request.evidence_refs)
            if self._row(db, request) is not None:
                return evidence
            placeholders = ','.join('?' for _ in request.evidence_refs)
            if db.execute(
                f'SELECT 1 FROM reflection_evidence WHERE {SCOPE} AND evidence_ref IN ({placeholders})',
                (*scope, *request.evidence_refs),
            ).fetchone():
                raise ReflectionConflict('evidence already belongs to a reflection')
            db.execute(
                'INSERT INTO reflections(profile_id,project_id,id,input_sha256,call_started) VALUES (?,?,?,?,NULL)',
                (*scope, key, digest(asdict(request))),
            )
            db.executemany(
                'INSERT INTO reflection_evidence VALUES (?,?,?,?)',
                ((*scope, ref, key) for ref in request.evidence_refs),
            )
        return evidence

    def start_call(self, request: ReflectionInput) -> None:
        with self.store.transaction() as db:
            row = self._row(db, request)
            if row is None or row['draft'] is not None or row['call_started'] is not None:
                raise ReflectionConflict('reflection callback is already recorded or unreserved')
            self.store.reflection_excerpts(db, request.scope, request.evidence_refs)
            db.execute(f'UPDATE reflections SET call_started=1 WHERE {SCOPE} AND id=?',
                       (*request.scope.key(), self.key(request)))

    def save_draft(self, request: ReflectionInput, draft: ReflectionDraft) -> None:
        self._publish(request, 'draft', encode(asdict(draft)))

    def save_candidate(self, request: ReflectionInput, candidate: CandidateRequest) -> None:
        self._publish(request, 'candidate', encode(asdict(candidate)))

    def save_job(self, request: ReflectionInput, job: JobRef) -> None:
        self._publish(request, 'job_id', job.job_id)

    def _publish(self, request: ReflectionInput, column: str, value: str) -> None:
        with self.store.transaction() as db:
            row = self._row(db, request)
            if row is None:
                raise RuntimeError('reflection must be reserved before publication')
            if row[column] is not None:
                if row[column] != value:
                    raise ReflectionConflict('reflection already has a different receipt')
                return
            db.execute(f'UPDATE reflections SET {column}=? WHERE {SCOPE} AND id=?',
                       (value, *request.scope.key(), self.key(request)))
