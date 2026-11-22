"""Owned read-only orchestration: selected instruction, plan, candidate, native Diff."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from threading import Event, Lock, Thread
import uuid

from harnessgrad.personal.adapters.contracts import HostBinding, SurfaceError, TargetSelection
from harnessgrad.personal.contracts import SurfaceRef
from harnessgrad.personal.model import ModelSession
from harnessgrad.personal.profile import ModelLimits, ModelSettings
from harnessgrad.personal.proposal_run_store import ProposalStopped, ProposalUnavailable
from harnessgrad.personal.workflow import plan_feedback, propose_candidate, question_id

INSTRUCTION = 'AGENTS.md'
LIMITS = ModelLimits(max_calls=3, execution_seconds=90, max_revisions=1)


class ProposalWorker:
    """One owned task at a time; an interrupted task never dispatches again automatically."""

    def __init__(self, store, adapter, settings: ModelSettings, *, fixture_client=None):
        self.store, self.adapter, self.settings = store, adapter, settings
        self.fixture_client = fixture_client
        self.owner = uuid.uuid4().hex
        self._lock = Lock()
        self._closing = Event()
        self._thread = None

    def close(self) -> None:
        with self._lock:
            self._closing.set()

    def _busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _start(self, project_id, feedback_id, root, resume):
        runs = self.store.proposal_runs
        self._thread = Thread(target=self._run, args=(project_id, feedback_id, root, resume),
                              name='personal-proposal', daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            runs.advance(feedback_id, self.owner, 'collecting', 'failed', result={'code': 'proposal_failed'})
        return runs.get(project_id, feedback_id, self.owner)

    def request(self, project_id: str, feedback_id: str, root: Path) -> dict:
        runs = self.store.proposal_runs
        with self._lock:
            prior = runs.get(project_id, feedback_id, self.owner)
            if prior is not None:
                return prior
            if self._closing.is_set():
                raise ProposalUnavailable('proposal_inactive')
            if self._busy():
                raise ProposalUnavailable('proposal_busy')
            runs.begin(project_id, feedback_id, self.owner)
            return self._start(project_id, feedback_id, root, None)

    def reply(self, project_id, feedback_id, root, question, answer):
        if isinstance(answer, str) and self.settings.method.api_key in answer:
            raise ValueError('reply must not contain the endpoint credential')
        runs = self.store.proposal_runs
        with self._lock:
            if self._closing.is_set():
                raise ProposalUnavailable('proposal_inactive')
            resume = runs.reply(project_id, feedback_id, self.owner, question, answer, busy=self._busy())
            if resume is None:
                return runs.get(project_id, feedback_id, self.owner)
            return self._start(project_id, feedback_id, root, resume)

    def _run(self, project_id, feedback_id, root, resume) -> None:
        runs = self.store.proposal_runs
        status = 'collecting'
        job_id, expected_snapshot = resume if resume is not None else (None, None)

        def advance(next_status, **fields):
            nonlocal status
            runs.advance(feedback_id, self.owner, status, next_status, **fields)
            status = next_status

        def check_open():
            if self._closing.is_set():
                raise ProposalUnavailable('proposal_inactive')
            if runs.get(project_id, feedback_id, self.owner)['status'] == 'cancelled':
                raise ProposalStopped('proposal cancelled')

        def guard_reservation(db, active_job_id):
            if self._closing.is_set():
                raise ProposalUnavailable('proposal_inactive')
            runs.require_call(db, feedback_id, self.owner, active_job_id, status)

        try:
            check_open()
            binding = HostBinding('default', project_id, 'project', root, None, sys.platform, 'bypass')
            target = TargetSelection('project-instructions', 'instruction', SurfaceRef('project', INSTRUCTION))
            snapshot = self.adapter.collect_selected(binding, (target,))
            if expected_snapshot is not None and expected_snapshot != snapshot.snapshot_sha256:
                raise SurfaceError('stale_snapshot', 'The original selected file changed before resumption.')
            texts = {row.surface: row.text for row in snapshot.files}
            instruction, = snapshot.surface_map
            selected = {'instruction': (instruction.virtual_path, texts[instruction.surface])}
            check_open()
            model = ModelSession(self.store, project_id, self.settings, limits=LIMITS, job_id=job_id,
                                 fixture=self.fixture_client is not None, client=self.fixture_client,
                                 reservation_guard=guard_reservation)
            advance('planning', job_id=model.job_id, snapshot_sha256=snapshot.snapshot_sha256)
            check_open()
            record = plan_feedback(model, project_id, feedback_id, clarified=resume is not None, **selected)
            plan = record['plan']
            check_open()
            decision = plan['decision']
            if decision != 'plan_ready':
                waiting = decision == 'needs_input' and resume is None
                result = {'question': plan['question'], 'plan_id': record['record_id'],
                          'fixture': record['fixture'],
                          'question_id': question_id(record['record_id']) if waiting else None}
                if not waiting:
                    self.store.models.complete(model.job_id)
                    if decision == 'needs_input':
                        decision = 'failed'
                        result['code'] = 'clarification_exhausted'
                with self._lock:
                    advance(decision, result=result)
                    self._thread = None
                return
            advance('proposing')
            check_open()
            candidate = propose_candidate(model, project_id, feedback_id, record['record_id'],
                                          clarified=resume is not None, **selected)
            check_open()
            advance('checking')
            prepared = self.adapter.prepare_extension(snapshot, candidate['files'])
            self.adapter.verify_selected(binding, snapshot)
            check_open()
            self.store.models.complete(model.job_id)
            advance('ready', result={
                'paths': [change.surface.path for change in prepared.changes], 'diff': prepared.diff,
                'snapshot_sha256': prepared.snapshot_sha256, 'prepared_sha256': prepared.prepared_sha256,
                'plan_id': record['record_id'], 'candidate_id': candidate['record_id'],
                'fixture': candidate['fixture'], 'applied': False, 'verified': False,
            })
        except ProposalStopped:
            return
        except (SurfaceError, ProposalUnavailable, ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            unavailable = isinstance(error, ProposalUnavailable)
            code = error.code if isinstance(error, (SurfaceError, ProposalUnavailable)) else 'proposal_failed'
            try:
                advance('interrupted' if unavailable else 'failed', result={'code': code})
            except ProposalStopped:
                pass
