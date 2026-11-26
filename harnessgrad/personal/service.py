"""Owned local use cases for feedback and read-only proposals."""
from __future__ import annotations

from pathlib import Path
import os
import stat

from harnessgrad.personal.adapters.codex import CodexAdapter
from harnessgrad.personal.contracts import decode, source_events
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.inputs import PersonalFeedback
from harnessgrad.personal.profile import ModelSettings
from harnessgrad.personal.proposal_jobs import INSTRUCTION, ProposalWorker
from harnessgrad.personal.proposal_run_store import ProposalUnavailable

REPOSITORY = Path(__file__).resolve().parents[2]


def plain_directory(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    for candidate in (absolute, *absolute.parents):
        if candidate.exists() and (candidate.is_symlink() or candidate.is_junction()):
            raise ValueError('selected directory must not traverse links or junctions')
    return absolute.resolve()


def state_directory(path: Path) -> Path:
    root = plain_directory(path)
    if root.is_relative_to(REPOSITORY):
        raise ValueError('personal state must be outside the checkout')
    root.mkdir(parents=True, exist_ok=True)
    for name in ('personal.sqlite3', 'personal.sqlite3-wal', 'personal.sqlite3-shm', 'connection.json'):
        target = root / name
        if target.exists() and (target.is_symlink() or target.stat().st_nlink != 1 or not target.is_file()):
            raise ValueError('personal state files must be ordinary single-link files')
    return root


def read_import(path: Path) -> dict:
    from harnessgrad.personal.contracts import MAX_BYTES
    # This reader accepts an explicit synthetic fixture, never a session directory.
    if path.is_symlink() or not path.is_file():
        raise ValueError('import must select one ordinary JSON fixture')
    with path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('import must be a single-link regular file')
        raw = handle.read(MAX_BYTES + 1)
        after = os.fstat(handle.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('import changed while reading')
    return decode(raw)


class PersonalService:
    def __init__(self, state_root: Path, *, codex: Path | None = None, managed_hooks: bool = False,
                 proposal_settings: ModelSettings | None = None, proposal_client=None):
        self.root = state_directory(state_root)
        self.store = EventStore(self.root / 'personal.sqlite3')
        self.codex = codex
        self.managed_hooks = managed_hooks
        self.adapter = CodexAdapter()
        self.capabilities = self.adapter.probe(None, managed_hooks=managed_hooks).as_dict()
        if proposal_settings is None and proposal_client is not None:
            raise ValueError('proposal fixtures need explicit model settings')
        self.proposals = (ProposalWorker(self.store, self.adapter, proposal_settings, fixture_client=proposal_client)
                          if proposal_settings is not None else None)

    def probe(self) -> dict:
        self.capabilities = self.adapter.probe(self.codex, managed_hooks=self.managed_hooks).as_dict()
        return self.capabilities

    def add_project(self, path: Path) -> dict:
        root = plain_directory(path)
        if not root.is_dir():
            raise ValueError('project must be an existing directory')
        if self.root.is_relative_to(root) or root.is_relative_to(self.root):
            raise ValueError('project and personal state must not overlap')
        return self.store.add_project(root)

    def import_session(self, project_id: str, value: dict) -> dict:
        return self.store.import_events(project_id, source_events(value))

    def submit_feedback(self, value: dict) -> dict:
        return self.store.feedback.submit(PersonalFeedback.from_dict(value))

    def proposal_capability(self) -> dict:
        if self.proposals is None:
            return {'status': 'unconfigured'}
        can_read = any(row['scope'] == 'codex.selected_surface_read' and row['status'] == 'ready'
                       for row in self.capabilities['checks'])
        if not can_read:
            return {'status': 'unavailable'}
        return {'status': 'ready', 'path': INSTRUCTION, 'model': self.proposals.settings.identity()}

    def proposal(self, project_id: str, feedback_id: str) -> dict | None:
        owner = self.proposals.owner if self.proposals is not None else None
        return self.store.proposal_runs.get(project_id, feedback_id, owner)

    def request_proposal(self, project_id: str, feedback_id: str) -> dict:
        prior = self.proposal(project_id, feedback_id)
        if prior is not None:
            return prior
        status = self.proposal_capability()['status']
        if status != 'ready':
            raise ProposalUnavailable('proposal_' + status)
        return self.proposals.request(project_id, feedback_id, self.store.project_root(project_id))

    def reply_to_proposal(self, project_id, feedback_id, question, answer):
        status = self.proposal_capability()['status']
        if status != 'ready':
            raise ProposalUnavailable('proposal_' + status)
        return self.proposals.reply(project_id, feedback_id, self.store.project_root(project_id), question, answer)

    def cancel_proposal(self, project_id, feedback_id):
        owner = self.proposals.owner if self.proposals is not None else None
        return self.store.proposal_runs.cancel(project_id, feedback_id, owner)

    def close(self) -> None:
        if self.proposals is not None:
            self.proposals.close()

    def snapshot(self) -> dict:
        usage = self.store.models.totals()
        feedback_count = self.store.feedback.count()
        return {'schema': 'harnessgrad.personal.stage-a.v1', 'stage': 'B2' if feedback_count else ('B1' if usage['jobs'] else 'A'),
                'read_only': True, 'model_calls': usage['logical_calls'], 'model_usage': usage,
                'feedback_count': feedback_count, 'fts_available': self.store.evidence.fts,
                'harness_changes': 0, 'projects': self.store.projects(),
                'capabilities': self.capabilities, 'proposal_generation': self.proposal_capability()}
