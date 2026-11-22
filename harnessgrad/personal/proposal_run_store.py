"""Project-scoped proposal receipts and replayable state transitions."""
from __future__ import annotations

from harnessgrad.personal.contracts import decode, encode, identifier

ACTIVE = ('collecting', 'planning', 'proposing', 'checking')
TERMINAL = ('ready', 'needs_input', 'no_change', 'failed', 'interrupted', 'cancelled')
PROPOSAL_RUN_DDL = (
    'CREATE TABLE proposal_runs (feedback_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, '
    'owner TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN '
    f'{(*ACTIVE, *TERMINAL)}), job_id TEXT REFERENCES jobs(id), result TEXT, snapshot_sha256 TEXT, '
    'FOREIGN KEY(feedback_id,project_id) REFERENCES feedback(id,project_id))'
)
PROPOSAL_EVENT_DDL = (
    'CREATE TABLE proposal_events (feedback_id TEXT NOT NULL REFERENCES proposal_runs(feedback_id), '
    'seq INTEGER NOT NULL CHECK(seq>0), status TEXT NOT NULL, PRIMARY KEY(feedback_id,seq))'
)
PROPOSAL_RUN_COLUMNS = ('feedback_id', 'project_id', 'owner', 'status', 'job_id', 'result', 'snapshot_sha256')
PROPOSAL_EVENT_COLUMNS = ('feedback_id', 'seq', 'status')


class ProposalUnavailable(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProposalStopped(RuntimeError):
    """A worker lost its expected state to an accepted cancellation."""


class ProposalRunStore:
    def __init__(self, events):
        self.events = events

    @staticmethod
    def _row(db, project_id, feedback_id):
        identifier(project_id)
        identifier(feedback_id)
        if not db.execute('SELECT 1 FROM feedback WHERE project_id=? AND id=?',
                          (project_id, feedback_id)).fetchone():
            raise ValueError('unknown feedback for selected project')
        return db.execute('SELECT * FROM proposal_runs WHERE project_id=? AND feedback_id=?',
                          (project_id, feedback_id)).fetchone()

    def get(self, project_id: str, feedback_id: str, owner: str | None) -> dict | None:
        with self.events.connection() as db:
            row = self._row(db, project_id, feedback_id)
        if row is None:
            return None
        status = row['status']
        if row['owner'] != owner and status in ACTIVE:
            status = 'interrupted'
        return {'feedback_id': feedback_id, 'status': status, 'job_id': row['job_id'],
                'result': decode(row['result']) if row['result'] is not None else None}

    def after(self, project_id: str, feedback_id: str, after: int) -> list[dict]:
        if type(after) is not int or not 0 <= after < 2**63:
            raise ValueError('invalid proposal event cursor')
        with self.events.connection() as db:
            self._row(db, project_id, feedback_id)
            return [dict(row) for row in db.execute('SELECT seq,status FROM proposal_events '
                'WHERE feedback_id=? AND seq>? ORDER BY seq LIMIT 50', (feedback_id, after))]

    @staticmethod
    def _event(db, feedback_id, status):
        db.execute('INSERT INTO proposal_events SELECT ?,coalesce(max(seq),0)+1,? '
                   'FROM proposal_events WHERE feedback_id=?', (feedback_id, status, feedback_id))

    def begin(self, project_id: str, feedback_id: str, owner: str) -> None:
        with self.events.connection() as db, self.events.transaction(db):
            db.execute('INSERT INTO proposal_runs VALUES (?,?,?,?,NULL,NULL,NULL)',
                       (feedback_id, project_id, owner, 'collecting'))
            self._event(db, feedback_id, 'collecting')

    def advance(self, feedback_id: str, owner: str, before: str, status: str, *,
                job_id: str | None = None, result: dict | None = None,
                snapshot_sha256: str | None = None) -> None:
        body = None if result is None else encode(result)
        if body is not None:
            decode(body)
        with self.events.connection() as db, self.events.transaction(db):
            updated = db.execute('UPDATE proposal_runs SET status=?, job_id=coalesce(?,job_id),result=?, '
                                 'snapshot_sha256=coalesce(?,snapshot_sha256) '
                                 'WHERE feedback_id=? AND owner=? AND status=?',
                                 (status, job_id, body, snapshot_sha256, feedback_id, owner, before))
            if updated.rowcount != 1:
                raise ProposalStopped('proposal run no longer owns the expected state')
            self._event(db, feedback_id, status)

    def require_call(self, db, feedback_id: str, owner: str, job_id: str, phase: str) -> None:
        """Check the run inside the model reservation's write transaction."""
        if phase not in ('planning', 'proposing') or not db.execute(
                'SELECT 1 FROM proposal_runs WHERE feedback_id=? AND owner=? AND job_id=? AND status=?',
                (feedback_id, owner, job_id, phase)).fetchone():
            raise ProposalStopped('proposal run no longer permits a model call')

    def reply(self, project_id, feedback_id, owner, question_id, answer, *, busy):
        identifier(question_id)
        if not isinstance(answer, str) or not answer.strip() or len(answer.encode('utf-8')) > 16384:
            raise ValueError('proposal reply must have 1 to 16 KiB of text')
        with self.events.connection() as db, self.events.transaction(db):
            row = self._row(db, project_id, feedback_id)
            if row is None:
                raise ProposalUnavailable('proposal_reply_unavailable')
            question = db.execute('SELECT status,response FROM questions WHERE id=? AND job_id=?',
                                  (question_id, row['job_id'])).fetchone()
            if question is None:
                raise ProposalUnavailable('proposal_reply_unavailable')
            if question['status'] == 'answered':
                if question['response'] != answer:
                    raise ProposalUnavailable('proposal_reply_conflict')
                return None  # A repeated answer acknowledges the original transition.
            receipt = decode(row['result']) if row['result'] is not None else {}
            if (row['status'] != 'needs_input' or not row['snapshot_sha256']
                    or receipt.get('question_id') != question_id):
                raise ProposalUnavailable('proposal_reply_unavailable')
            if busy:
                raise ProposalUnavailable('proposal_busy')
            db.execute("UPDATE questions SET status='answered',response=? WHERE id=?", (answer, question_id))
            db.execute("UPDATE proposal_runs SET owner=?,status='collecting',result=NULL WHERE feedback_id=?",
                       (owner, feedback_id))
            self._event(db, feedback_id, 'collecting')
            return row['job_id'], row['snapshot_sha256']

    def cancel(self, project_id, feedback_id, owner):
        with self.events.connection() as db, self.events.transaction(db):
            row = self._row(db, project_id, feedback_id)
            if row is None:
                raise ProposalUnavailable('proposal_cancel_unavailable')
            if row['status'] == 'needs_input' or (row['owner'] == owner and row['status'] in ACTIVE):
                db.execute("UPDATE proposal_runs SET status='cancelled',result=NULL WHERE feedback_id=?",
                           (feedback_id,))
                self._event(db, feedback_id, 'cancelled')
        return self.get(project_id, feedback_id, owner)
