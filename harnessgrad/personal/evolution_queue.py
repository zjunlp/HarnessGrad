"""Durable, scoped implementation of the v1 evolution queue."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path
import sqlite3
import uuid

from harnessgrad.personal.contracts import digest, encode
from harnessgrad.personal.evolution_contracts import (
    ArtifactPort,
    CandidateRequest,
    EventPage,
    JobEvent,
    JobRef,
    JobStatus,
    JobView,
    PortRejected,
    Scope,
    event_cursor,
)

APPLICATION_ID = 0x48474331  # HarnessGrad C1
SCHEMA_VERSION = 1
JOB_COLUMNS = (
    'profile_id', 'project_id', 'id', 'idempotency_key', 'request_sha256',
    'request', 'status', 'reason_code', 'candidate_id', 'question_id',
    'comparison_ref',
)
EVENT_COLUMNS = ('profile_id', 'project_id', 'job_id', 'seq', 'status', 'reason_code')
DDL = (
    'CREATE TABLE evolution_jobs ('
    'profile_id TEXT NOT NULL, project_id TEXT NOT NULL, id TEXT NOT NULL, '
    'idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, request TEXT NOT NULL, '
    'status TEXT NOT NULL, reason_code TEXT, candidate_id TEXT, question_id TEXT, comparison_ref TEXT, '
    'PRIMARY KEY(profile_id,project_id,id), '
    'UNIQUE(profile_id,project_id,idempotency_key))',
    'CREATE UNIQUE INDEX evolution_jobs_global_id ON evolution_jobs(id)',
    'CREATE TABLE evolution_job_events ('
    'profile_id TEXT NOT NULL, project_id TEXT NOT NULL, job_id TEXT NOT NULL, '
    'seq INTEGER NOT NULL CHECK(seq>0), status TEXT NOT NULL, reason_code TEXT, '
    'PRIMARY KEY(profile_id,project_id,job_id,seq), '
    'FOREIGN KEY(profile_id,project_id,job_id) '
    'REFERENCES evolution_jobs(profile_id,project_id,id))',
)


class EvolutionQueueStore:
    """C-owned SQLite state; callers use :class:`EvolutionQueue` instead."""

    def __init__(self, path: Path):
        self.path = path
        with self.transaction() as db:
            identity = db.execute('PRAGMA application_id').fetchone()[0]
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if (identity, version) == (APPLICATION_ID, SCHEMA_VERSION):
                self._require_schema(db)
                return
            if identity or version or db.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone():
                raise ValueError('not a recognized evolution queue database')
            for statement in DDL:
                db.execute(statement)
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    @staticmethod
    def _require_schema(db: sqlite3.Connection) -> None:
        expected = {'evolution_jobs': JOB_COLUMNS, 'evolution_job_events': EVENT_COLUMNS}
        objects = {(row['type'], row['name']) for row in db.execute(
            "SELECT type,name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
        )}
        expected_objects = {('table', name) for name in expected} | {
            ('index', 'evolution_jobs_global_id'),
        }
        if objects != expected_objects:
            raise ValueError('unrecognized evolution queue schema')
        for table, columns in expected.items():
            actual = tuple(row['name'] for row in db.execute(f'PRAGMA table_info({table})'))
            if actual != columns:
                raise ValueError('unrecognized evolution queue schema')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('invalid evolution queue references')

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


class EvolutionQueue:
    """Validate public references and persist a replay-safe candidate job."""

    def __init__(
        self,
        store: EvolutionQueueStore,
        artifacts: ArtifactPort,
        *,
        read_evidence: Callable[[Scope, str], object],
        scenario_available: Callable[[Scope, str], bool],
    ):
        self.store = store
        self.artifacts = artifacts
        self.read_evidence = read_evidence
        self.scenario_available = scenario_available

    @staticmethod
    def _job(row: sqlite3.Row) -> JobRef:
        return JobRef(row['profile_id'], row['project_id'], row['id'])

    @classmethod
    def _view(cls, row: sqlite3.Row) -> JobView:
        return JobView(cls._job(row), row['status'], row['reason_code'], row['candidate_id'],
                       row['question_id'], row['comparison_ref'])

    @staticmethod
    def _prior(db: sqlite3.Connection, request: CandidateRequest) -> sqlite3.Row | None:
        return db.execute(
            'SELECT * FROM evolution_jobs WHERE profile_id=? AND project_id=? AND idempotency_key=?',
            (*request.scope.key(), request.idempotency_key),
        ).fetchone()

    def _validate_new(self, request: CandidateRequest) -> None:
        scope = request.scope
        try:
            snapshot = self.artifacts.read_artifact(scope, request.snapshot_ref, kind='virtual_snapshot')
            candidate = self.artifacts.read_artifact(scope, request.candidate_ref, kind='text_candidate')
            for ref in request.evidence_refs:
                self.read_evidence(scope, ref)
        except PortRejected:
            raise
        except (KeyError, LookupError, PermissionError):
            raise PortRejected('out_of_scope') from None
        if snapshot.sha256 != request.snapshot_sha256:
            raise PortRejected('stale_snapshot')
        selected = snapshot.payload.get('target_ids')
        if not isinstance(selected, (list, tuple)) or tuple(selected) != request.target_ids:
            raise PortRejected('stale_snapshot')
        if candidate.sha256 != request.candidate_sha256:
            raise PortRejected('stale_snapshot')
        files = candidate.payload.get('files')
        if (set(candidate.payload) != {'files'} or not isinstance(files, Mapping)
                or not files or any(not isinstance(path, str) or not isinstance(text, str)
                                    for path, text in files.items())
                or digest({'files': dict(files)}) != request.candidate_sha256):
            raise PortRejected('invalid_request')
        if not self.scenario_available(scope, request.scenario_id):
            raise PortRejected('invalid_request')

    def submit_candidate(self, request: CandidateRequest) -> JobRef:
        if not isinstance(request, CandidateRequest):
            raise PortRejected('invalid_request')
        fingerprint = digest(asdict(request))
        # Check an accepted request before resolving mutable references.  This
        # lets a caller recover the original receipt after a lost acknowledgement.
        with closing(self.store.connection()) as db:
            prior = self._prior(db, request)
        if prior is not None:
            if prior['request_sha256'] != fingerprint:
                raise PortRejected('idempotency_conflict')
            return self._job(prior)

        self._validate_new(request)
        job = JobRef(*request.scope.key(), 'evo_' + uuid.uuid4().hex)
        try:
            with self.store.transaction() as db:
                prior = self._prior(db, request)
                if prior is not None:
                    if prior['request_sha256'] != fingerprint:
                        raise PortRejected('idempotency_conflict')
                    return self._job(prior)
                db.execute(
                    'INSERT INTO evolution_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (*request.scope.key(), job.job_id, request.idempotency_key, fingerprint,
                     encode(asdict(request)), 'collecting', None, request.candidate_ref, None, None),
                )
                db.executemany(
                    'INSERT INTO evolution_job_events VALUES (?,?,?,?,?,?)',
                    ((*request.scope.key(), job.job_id, 1, 'created', None),
                     (*request.scope.key(), job.job_id, 2, 'collecting', None)),
                )
        except sqlite3.IntegrityError:
            # A concurrent submitter may have committed between validation and
            # our transaction.  Resolve only the exact accepted request.
            with closing(self.store.connection()) as db:
                prior = self._prior(db, request)
            if prior is None or prior['request_sha256'] != fingerprint:
                raise PortRejected('idempotency_conflict') from None
            return self._job(prior)
        return job

    def _scoped_row(self, job: JobRef) -> sqlite3.Row:
        if not isinstance(job, JobRef):
            raise PortRejected('out_of_scope')
        with closing(self.store.connection()) as db:
            row = db.execute('SELECT * FROM evolution_jobs WHERE id=?', (job.job_id,)).fetchone()
        if row is None or (row['profile_id'], row['project_id']) != job.scope.key():
            raise PortRejected('out_of_scope')
        return row

    def read_job(self, job: JobRef) -> JobView:
        return self._view(self._scoped_row(job))

    def read_events(self, job: JobRef, *, after: int) -> EventPage:
        after = event_cursor(after)
        self._scoped_row(job)
        with closing(self.store.connection()) as db:
            rows = db.execute(
                'SELECT seq,status,reason_code FROM evolution_job_events '
                'WHERE profile_id=? AND project_id=? AND job_id=? AND seq>? ORDER BY seq LIMIT 50',
                (*job.scope.key(), job.job_id, after),
            ).fetchall()
        events = tuple(JobEvent(job, row['seq'], row['status'], row['reason_code']) for row in rows)
        return EventPage(events, events[-1].seq if events else after)

    def transition(
        self,
        job: JobRef,
        status: JobStatus,
        *,
        reason_code: str | None = None,
        question_id: str | None = None,
        comparison_ref: str | None = None,
    ) -> JobView:
        """Internal C orchestration hook used by later reproduction stages."""
        current = self._scoped_row(job)
        view = JobView(job, status, reason_code, current['candidate_id'], question_id, comparison_ref)
        with self.store.transaction() as db:
            seq = db.execute(
                'SELECT coalesce(max(seq),0)+1 FROM evolution_job_events '
                'WHERE profile_id=? AND project_id=? AND job_id=?',
                (*job.scope.key(), job.job_id),
            ).fetchone()[0]
            db.execute(
                'UPDATE evolution_jobs SET status=?,reason_code=?,question_id=?,comparison_ref=? '
                'WHERE profile_id=? AND project_id=? AND id=?',
                (status, reason_code, question_id, comparison_ref, *job.scope.key(), job.job_id),
            )
            db.execute('INSERT INTO evolution_job_events VALUES (?,?,?,?,?,?)',
                       (*job.scope.key(), job.job_id, seq, status, reason_code))
        return view
