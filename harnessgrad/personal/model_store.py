"""Persistent model reservations on the personal event store's transactions.

No transport, prompts, replies or credentials belong in this ledger. A caller
holds the per-job execution lock before recovering or starting a lease.
"""
from __future__ import annotations

import uuid

from harnessgrad.personal.contracts import decode, digest, encode, identifier
from harnessgrad.personal.profile import ModelLimits

MODEL_DDL = (
    'CREATE TABLE model_budgets (job_id TEXT PRIMARY KEY REFERENCES jobs(id), '
    'profile_id TEXT NOT NULL, config TEXT NOT NULL, limits TEXT NOT NULL, '
    'calls INTEGER NOT NULL DEFAULT 0 CHECK(calls>=0), '
    'revisions INTEGER NOT NULL DEFAULT 0 CHECK(revisions>=0), '
    'elapsed REAL NOT NULL DEFAULT 0 CHECK(elapsed>=0), active_since REAL, lease TEXT, '
    'recoveries INTEGER NOT NULL DEFAULT 0 CHECK(recoveries>=0), '
    'fixture INTEGER NOT NULL CHECK(fixture IN (0,1)))',
    'CREATE TABLE model_calls (job_id TEXT NOT NULL REFERENCES model_budgets(job_id), '
    'seq INTEGER NOT NULL CHECK(seq>0), lease TEXT NOT NULL, stage TEXT NOT NULL, '
    'input_sha256 TEXT NOT NULL, input_bytes INTEGER NOT NULL CHECK(input_bytes>=0), '
    "status TEXT NOT NULL CHECK(status IN ('reserved','completed','failed','unknown')), "
    'usage TEXT, failure_kind TEXT, PRIMARY KEY(job_id,seq))',
)


class ModelStore:
    def __init__(self, events):
        self.events = events

    @staticmethod
    def _row(db, job_id):
        identifier(job_id)
        row = db.execute('SELECT b.*,j.project_id,j.status AS job_status FROM model_budgets b '
                         'JOIN jobs j ON j.id=b.job_id WHERE b.job_id=?', (job_id,)).fetchone()
        if row is None:
            raise ValueError('unknown personal model job')
        return row

    @staticmethod
    def _active(row, lease):
        if row['lease'] != lease or row['active_since'] is None:
            raise ValueError('model execution lease does not match')

    def prepare(self, project_id: str, profile_id: str, config: dict, limits: ModelLimits,
                *, job_id: str | None = None, fixture: bool = False) -> str:
        identifier(profile_id)
        if type(fixture) is not bool:
            raise ValueError('fixture mode must be explicit')
        if set(config) != {'endpoint', 'model', 'request_timeout_seconds',
                           'credential_ref', 'credential_present'}:
            raise ValueError('model identity contains unsupported fields')
        config_body, limit_body = encode(config), encode(limits.as_dict())
        with self.events.connection() as db, self.events.transaction(db):
            self.events.require_project(db, project_id)
            if job_id is not None:
                row = self._row(db, job_id)
                if (row['project_id'] != project_id or row['profile_id'] != profile_id
                        or row['config'] != config_body or row['limits'] != limit_body
                        or row['fixture'] != int(fixture)):
                    raise ValueError('model job settings differ from its frozen snapshot')
                return job_id
            job_id = 'j_' + uuid.uuid4().hex
            binding = {'profile_id': profile_id, 'config': config, 'limits': limits.as_dict(),
                       'fixture': fixture}
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',
                       (job_id, project_id, 'model', 'ready', digest(binding)))
            db.execute('INSERT INTO model_budgets(job_id,profile_id,config,limits,fixture) VALUES (?,?,?,?,?)',
                       (job_id, profile_id, config_body, limit_body, int(fixture)))
            self.events.add_event(db, job_id, 'model.prepared', binding)
        return job_id

    def start(self, job_id: str, lease: str, now: float) -> float:
        """Recover only after the OS execution lock proves the prior owner is gone."""
        exhausted = False
        with self.events.connection() as db, self.events.transaction(db):
            row = self._row(db, job_id)
            if row['job_status'] == 'completed':
                raise ValueError('model job is already completed')
            limits = ModelLimits(**decode(row['limits']))
            elapsed = row['elapsed']
            if row['lease'] is not None:
                charge = (now - row['active_since'] if now >= row['active_since']
                          else max(0, limits.execution_seconds - elapsed))
                elapsed += charge
                db.execute("UPDATE model_calls SET status='unknown',failure_kind='interrupted' "
                           "WHERE job_id=? AND status='reserved'", (job_id,))
                db.execute('UPDATE model_budgets SET recoveries=recoveries+1 WHERE job_id=?', (job_id,))
                self.events.add_event(db, job_id, 'model.recovered',
                                      {'conservative_execution_charge': charge})
            remaining = limits.execution_seconds - elapsed
            if remaining <= 0:
                exhausted = True
                db.execute('UPDATE model_budgets SET elapsed=?,lease=NULL,active_since=NULL WHERE job_id=?',
                           (elapsed, job_id))
                db.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
                self.events.add_event(db, job_id, 'model.execution_exhausted', {})
            else:
                db.execute('UPDATE model_budgets SET elapsed=?,lease=?,active_since=? WHERE job_id=?',
                           (elapsed, lease, now, job_id))
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
                self.events.add_event(db, job_id, 'model.execution_started', {})
        if exhausted:
            raise ValueError('model execution budget exhausted')
        return remaining

    def pause(self, job_id: str, lease: str, elapsed: float, *, failed: bool = False):
        with self.events.connection() as db, self.events.transaction(db):
            row = self._row(db, job_id)
            self._active(row, lease)
            db.execute('UPDATE model_budgets SET elapsed=elapsed+?,lease=NULL,active_since=NULL WHERE job_id=?',
                       (max(0, elapsed), job_id))
            db.execute('UPDATE jobs SET status=? WHERE id=?', ('failed' if failed else 'paused', job_id))
            self.events.add_event(db, job_id, 'model.execution_paused',
                                  {'execution_seconds': max(0, elapsed), 'failed': failed})

    def reserve(self, job_id: str, lease: str, stage: str, input_sha256: str,
                input_bytes: int, now: float, *, guard=None) -> int:
        """Run the optional guard with the active write connection and job ID."""
        identifier(stage)
        with self.events.connection() as db, self.events.transaction(db):
            if guard is not None:
                guard(db, job_id)
            row = self._row(db, job_id)
            self._active(row, lease)
            limits = ModelLimits(**decode(row['limits']))
            if row['elapsed'] + max(0, now - row['active_since']) >= limits.execution_seconds:
                raise ValueError('model execution budget exhausted')
            if row['calls'] >= limits.max_calls:
                raise ValueError('logical model call limit reached')
            if type(input_bytes) is not int or not 0 <= input_bytes <= limits.max_input_bytes:
                raise ValueError('model input exceeds the task byte limit')
            seq = row['calls'] + 1
            db.execute('UPDATE model_budgets SET calls=? WHERE job_id=?', (seq, job_id))
            db.execute('INSERT INTO model_calls(job_id,seq,lease,stage,input_sha256,input_bytes,status) '
                       "VALUES (?,?,?,?,?,?,'reserved')", (job_id, seq, lease, stage, input_sha256, input_bytes))
            self.events.add_event(db, job_id, 'model.call_reserved',
                                  {'call': seq, 'stage': stage, 'input_sha256': input_sha256,
                                   'input_bytes': input_bytes})
        return seq

    def finish(self, job_id: str, lease: str, seq: int, usage: dict, *,
               status: str, failure_kind: str | None = None):
        names = {'prompt_tokens', 'completion_tokens', 'transport_attempts', 'unknown_transport_attempts'}
        if (set(usage) != names or any(v is not None and (type(v) is not int or v < 0)
                                       for v in usage.values())
                or status not in ('completed', 'failed', 'unknown')
                or failure_kind not in (None, 'transport', 'json', 'interrupted', 'deadline')):
            raise ValueError('invalid model accounting record')
        with self.events.connection() as db, self.events.transaction(db):
            row = self._row(db, job_id)
            self._active(row, lease)
            cursor = db.execute('UPDATE model_calls SET status=?,usage=?,failure_kind=? '
                                "WHERE job_id=? AND seq=? AND lease=? AND status='reserved'",
                                (status, encode(usage), failure_kind, job_id, seq, lease))
            if cursor.rowcount != 1:
                raise ValueError('model reservation is missing or already finalized')
            self.events.add_event(db, job_id, 'model.call_finished',
                                  {'call': seq, 'status': status, 'usage': usage,
                                   'failure_kind': failure_kind})

    def reserve_revision(self, job_id: str, lease: str) -> int:
        with self.events.connection() as db, self.events.transaction(db):
            row = self._row(db, job_id)
            self._active(row, lease)
            limits = ModelLimits(**decode(row['limits']))
            if row['revisions'] >= limits.max_revisions:
                raise ValueError('candidate revision limit reached')
            revision = row['revisions'] + 1
            db.execute('UPDATE model_budgets SET revisions=? WHERE job_id=?', (revision, job_id))
            self.events.add_event(db, job_id, 'model.revision_reserved', {'revision': revision})
        return revision

    def complete(self, job_id: str):
        with self.events.connection() as db, self.events.transaction(db):
            row = self._row(db, job_id)
            if row['lease'] is not None:
                raise ValueError('pause model execution before completing the job')
            if row['job_status'] != 'completed':
                db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
                self.events.add_event(db, job_id, 'model.completed', {})

    def usage(self, job_id: str) -> dict:
        with self.events.connection() as db:
            row = self._row(db, job_id)
            limits = ModelLimits(**decode(row['limits']))
            calls = []
            for call in db.execute('SELECT seq,stage,input_sha256,input_bytes,status,usage,failure_kind '
                                   'FROM model_calls WHERE job_id=? ORDER BY seq', (job_id,)):
                item = dict(call)
                raw_usage = item.pop('usage')
                usage = decode(raw_usage) if raw_usage is not None else {
                    key: None for key in ('prompt_tokens', 'completion_tokens',
                                         'transport_attempts', 'unknown_transport_attempts')}
                calls.append({**item, **usage})
        def total(key):
            return sum(c[key] for c in calls) if all(c[key] is not None for c in calls) else None
        unknown = total('unknown_transport_attempts')
        return {'job_id': job_id, 'profile_id': row['profile_id'], 'fixture': bool(row['fixture']),
                'status': row['job_status'], 'logical_calls': row['calls'],
                'transport_attempts': total('transport_attempts'),
                'observed_transport_attempts': sum(c['transport_attempts'] or 0 for c in calls),
                'unknown_transport_attempts': unknown,
                'prompt_tokens': total('prompt_tokens') if unknown == 0 else None,
                'completion_tokens': total('completion_tokens') if unknown == 0 else None,
                'execution_seconds': row['elapsed'], 'execution_active': row['lease'] is not None,
                'recovered_executions': row['recoveries'], 'revisions': row['revisions'],
                'remaining_calls': limits.max_calls - row['calls'],
                'limits': limits.as_dict(), 'model': decode(row['config']), 'calls': calls}

    def totals(self) -> dict:
        with self.events.connection() as db:
            row = db.execute('SELECT count(*) AS jobs,coalesce(sum(CASE WHEN fixture=0 THEN calls ELSE 0 END),0) '
                             'AS logical_calls,coalesce(sum(CASE WHEN fixture=1 THEN calls ELSE 0 END),0) '
                             'AS fixture_calls FROM model_budgets').fetchone()
            return dict(row)
