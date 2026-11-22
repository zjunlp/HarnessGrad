"""D1 evidence to a D2 text candidate and a scoped, idempotent fake C queue."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from harnessgrad.personal.contracts import decode, digest, encode, source_events
from harnessgrad.personal.evidence import EvidenceSelection
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.fixtures import ScriptedModelClient, example
from harnessgrad.personal.learning.contracts import LearningPolicyUpdate, LearningScope, SourceItem
from harnessgrad.personal.learning.reflection import SYSTEM, ReflectionService
from harnessgrad.personal.learning.reflection_contracts import (
    CANDIDATE_SCHEMA, ArtifactRef, ArtifactView, ExistingExperience, JobRef, PortRejected,
    ReflectionInput, SnapshotRef,
)
from harnessgrad.personal.learning.service import LearningService
from harnessgrad.personal.learning.source import SelectedImportSource
from harnessgrad.personal.learning.store import LearningStore
from harnessgrad.personal.model import ModelSession
from harnessgrad.personal.profile import load_model_settings

SCOPE = LearningScope('test-profile', 'project')
SOURCE = 'public-fixture-v1'
SKILL = 'skills/' + 'a' * 64 + '.md'
OLD = '- Run Python tests before release.'
NEW = '- After changing Python code, run the related tests before reporting completion.'
AGENTS = '# Project instructions\r\n\r\n' + OLD + '\r\n- Keep changelogs concise.\r\n'
SKILL_PREFIX = '---\nname: verify\ndescription: Verify changed files.\n---\n'


class OfflineC:
    def __init__(self, files):
        self.snapshot = SnapshotRef('frozen', digest(files), tuple(f'target-{i}' for i in range(len(files))))
        self.files = files
        self.evidence = set()
        self.artifacts = {}
        self.jobs = {}
        self.registrations = 0
        self.submissions = []
        self.drop_registration = False
        self.drop_ack = False

    def read_artifact(self, scope, ref, *, kind):
        if scope != SCOPE or ref != self.snapshot.snapshot_ref:
            raise PortRejected('out_of_scope')
        assert kind == 'virtual_snapshot'
        return ArtifactView(ref, self.snapshot.snapshot_sha256, kind, {'files': self.files.copy()})

    def register_candidate(self, scope, snapshot_ref, files):
        assert scope == SCOPE and snapshot_ref == self.snapshot.snapshot_ref
        assert files.keys() == self.files.keys()
        self.registrations += 1
        body = {'files': dict(files)}
        sha = digest(body)
        ref = 'candidate-' + sha
        self.artifacts[ref] = body
        if self.drop_registration:
            self.drop_registration = False
            raise ConnectionError('lost artifact acknowledgement')
        return ArtifactRef(ref, sha, 'text_candidate')

    def submit_candidate(self, request):
        self.submissions.append(request)
        assert (request.profile_id, request.project_id) == SCOPE.key()
        assert request.schema == CANDIDATE_SCHEMA
        assert set(asdict(request)) == {
            'schema', 'profile_id', 'project_id', 'target_ids', 'snapshot_ref', 'snapshot_sha256',
            'candidate_ref', 'candidate_sha256', 'evidence_refs', 'intent', 'conditions',
            'scenario_id', 'idempotency_key',
        }
        assert set(request.evidence_refs) <= self.evidence
        if prior := self.jobs.get(request.idempotency_key):
            if prior[0] != request:
                raise PortRejected('idempotency_conflict')
            return prior[1]
        assert request.snapshot_sha256 == self.snapshot.snapshot_sha256
        assert request.target_ids == self.snapshot.target_ids
        assert request.candidate_sha256 == digest(self.artifacts[request.candidate_ref])
        job = JobRef(*SCOPE.key(), 'job-fixture')
        self.jobs[request.idempotency_key] = request, job
        if self.drop_ack:
            self.drop_ack = False
            raise ConnectionError('lost queue acknowledgement')
        return job


def source_item(event_id, text=None, *, kind='reusable'):
    fixture = example()
    row = {**fixture['events'][2], 'event_id': event_id}
    if text is not None:
        row['text'] = text
    (event,) = source_events({**fixture, 'events': [row]})
    start = event.text.index('|') + 1 if '|' in event.text else 0
    selection = EvidenceSelection(SOURCE, event.session_id, event.event_id, digest(row), start, len(event.text))
    return SourceItem(event, selection, kind, task_boundary=True)


def setup(tmp_path, *, items=None, files=None, path='instruction.md', kind='rule', prior=True):
    items = items if items is not None else (source_item('selected', 'not-selected|修改 Python 后先跑相关测试'),)
    store = LearningStore(tmp_path / 'learning.sqlite3')
    update = LearningPolicyUpdate(SCOPE, 0, True, (SOURCE,), 0, 10, 'configure', 'confirmation')
    d1 = LearningService(store, SelectedImportSource(SCOPE, SOURCE, items),
                         confirm_policy=lambda request: request == update)
    d1.update_learning_policy(update)
    source = d1.poll_source(SCOPE, SOURCE, now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    refs = tuple(ref for row in source.receipts if row.decision.status == 'triggered'
                 for ref in row.decision.evidence_refs)
    c = OfflineC(files if files is not None else {'instruction.md': AGENTS})
    c.evidence.update(refs)
    existing = (ExistingExperience(SCOPE, c.snapshot.snapshot_sha256, 'python-tests', kind,
                                   path, OLD, 'rule-version-1'),) if prior else ()
    reply = {'decision': 'propose', 'topic_id': 'python-tests', 'kind': kind, 'path': path, 'text': NEW,
             'conditions': ['changes Python code'], 'criterion': 'Observe a related test run before the final reply.'}
    prompts = []

    def ask(prompt):
        prompts.append(decode(prompt))
        return reply

    def service(*, callback=ask, reopen=False, catalog=existing):
        return ReflectionService(LearningStore(store.path) if reopen else store, c, c,
                                 ask=callback, read_existing=lambda scope, snapshot: catalog)

    def request(evidence=None):
        return ReflectionInput(SCOPE, refs if evidence is None else evidence, c.snapshot, 'python-edit',
                               ('Keep the rule local to this project.',))

    return SimpleNamespace(store=store, c=c, prompts=prompts, source=source, service=service,
                           request=request, reply=reply, update=update, existing=existing)


@pytest.mark.parametrize('kind,path', [('rule', 'instruction.md'), ('memory', 'instruction.md'), ('skill', SKILL)])
def test_d2_revises_existing_text_and_submits_one_scoped_candidate(tmp_path, kind, path):
    files = {'instruction.md': AGENTS, SKILL: SKILL_PREFIX + OLD + '\n'}
    fixture = setup(tmp_path, files=files, path=path, kind=kind)
    original = tmp_path / 'AGENTS.md'
    original.write_bytes(AGENTS.encode())
    service = fixture.service()
    request = fixture.request()
    decision = service.reflect(request)
    assert decision.status == 'candidate_ready' and decision.candidate is not None
    assert not fixture.c.submissions
    receipt = service.submit(request)
    assert receipt.decision == decision and receipt.job == JobRef(*SCOPE.key(), 'job-fixture')
    assert receipt.experience.prior_version_ref == 'rule-version-1'
    assert receipt.experience.criterion == fixture.reply['criterion']
    assert decision.candidate.conditions == ('changes Python code',)
    assert decision.candidate.intent == 'preference' and decision.candidate.scenario_id == 'python-edit'
    assert fixture.c.artifacts[decision.candidate.candidate_ref]['files'] == {
        name: text.replace(OLD, NEW) if name == path else text for name, text in files.items()
    }
    assert original.read_bytes() == AGENTS.encode()
    assert fixture.service(reopen=True).submit(request) == receipt
    assert fixture.c.registrations == len(fixture.c.submissions) == len(fixture.prompts) == 1
    prompt = fixture.prompts[0]
    assert prompt['evidence'] == [{'ref': request.evidence_refs[0], 'provenance': 'user_quote',
                                   'text': '修改 Python 后先跑相关测试'}]
    assert prompt['constraints'] == list(request.constraints)


def test_identical_experience_is_no_change(tmp_path):
    fixture = setup(tmp_path)
    fixture.reply['text'] = OLD
    result = fixture.service().submit(fixture.request())
    assert (result.decision.status, result.decision.reason_code) == ('no_change', 'already_present')
    assert fixture.service(reopen=True).submit(fixture.request()) == result
    assert len(fixture.prompts) == 1 and not fixture.c.artifacts and not fixture.c.jobs


@pytest.mark.parametrize('change', [{'kind': 'virtual_snapshot'}, {'sha256': '0' * 64}])
def test_candidate_registration_rejects_mismatched_artifact(tmp_path, monkeypatch, change):
    fixture = setup(tmp_path)
    register = fixture.c.register_candidate
    monkeypatch.setattr(fixture.c, 'register_candidate', lambda *args: replace(register(*args), **change))

    receipt = fixture.service().submit(fixture.request())

    assert receipt.decision.reason_code == 'invalid_candidate_artifact'
    assert receipt.decision.candidate is None and receipt.job is None
    assert not fixture.c.submissions and not fixture.c.jobs


def test_revision_does_not_create_a_second_copy_of_an_existing_rule(tmp_path):
    fixture = setup(tmp_path, files={'instruction.md': AGENTS + '\r\n' + NEW + '\r\n'})
    result = fixture.service().submit(fixture.request())
    assert (result.decision.status, result.decision.reason_code) == ('deferred', 'duplicate_existing_experience')
    assert not fixture.c.artifacts and not fixture.c.jobs


def test_partial_line_is_not_a_valid_revision_anchor(tmp_path):
    fixture = setup(tmp_path, files={'instruction.md': '# Instructions\n> Deprecated example: ' + OLD + '\n'})
    result = fixture.service().submit(fixture.request())
    assert result.decision.reason_code == 'stale_snapshot'
    assert not fixture.prompts and not fixture.c.artifacts


def test_new_text_uses_only_the_selected_project_surface(tmp_path):
    fixture = setup(tmp_path, files={'instruction.md': '# Instructions\n'}, prior=False)
    receipt = fixture.service().submit(fixture.request())
    assert receipt.experience.prior_version_ref is None
    candidate = receipt.decision.candidate
    assert fixture.c.artifacts[candidate.candidate_ref]['files'] == {'instruction.md': '# Instructions\n\n' + NEW + '\n'}


def test_retries_reuse_frozen_output_and_original_job(tmp_path):
    fixture = setup(tmp_path)
    fixture.c.drop_registration = fixture.c.drop_ack = True
    request = fixture.request()
    assert fixture.service().submit(request).decision.reason_code == 'artifact_unavailable'
    assert fixture.service(reopen=True).submit(request).decision.reason_code == 'queue_unavailable'
    receipt = fixture.service(reopen=True).submit(request)
    assert receipt.job == JobRef(*SCOPE.key(), 'job-fixture')
    assert len(fixture.prompts) == len(fixture.c.artifacts) == len(fixture.c.jobs) == 1
    assert fixture.c.registrations == 2
    assert fixture.c.submissions == [receipt.decision.candidate] * 2
    changed = fixture.service().submit(replace(request, scenario_id='another-scenario'))
    assert changed.decision.reason_code == 'reflection_input_conflict'


@pytest.mark.parametrize('phase', ['register_candidate', 'submit_candidate'])
def test_concurrent_publishers_do_not_repeat_the_port_call(tmp_path, monkeypatch, phase):
    fixture = setup(tmp_path)
    request = fixture.request()
    started, resume = Event(), Event()
    call = getattr(fixture.c, phase)

    def delayed(*args):
        if not started.is_set():
            started.set()
            assert resume.wait(5)
        return call(*args)

    monkeypatch.setattr(fixture.c, phase, delayed)
    with ThreadPoolExecutor(max_workers=1) as worker:
        task = worker.submit(fixture.service().submit, request)
        try:
            assert started.wait(5)
            busy = fixture.service(reopen=True).submit(request)
            assert busy.decision.reason_code == 'reflection_in_progress'
        finally:
            resume.set()
        receipt = task.result()
    assert fixture.service(reopen=True).submit(request) == receipt
    assert fixture.c.registrations == len(fixture.c.submissions) == len(fixture.prompts) == 1


@pytest.mark.parametrize('phase', ['after_claim', 'before_draft_commit', 'after_draft_commit'])
def test_reflection_recovers_after_process_exit(tmp_path, phase):
    fixture = setup(tmp_path)
    service = fixture.service()
    request = fixture.request()
    script = """
import os
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(Path(sys.argv[1]) / 'tests' / 'quality'))

from harnessgrad.personal.contracts import decode
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.reflection import ReflectionService
from harnessgrad.personal.learning.reflection_contracts import ExistingExperience, ReflectionInput, SnapshotRef
from harnessgrad.personal.learning.store import LearningStore
from test_personal_reflection import AGENTS, OLD, OfflineC

body = decode(sys.argv[3])
scope = LearningScope(**body['scope'])
snap = body['snapshot']
snapshot = SnapshotRef(snap['snapshot_ref'], snap['snapshot_sha256'], tuple(snap['target_ids']))
request = ReflectionInput(scope, tuple(body['evidence_refs']), snapshot, body['scenario_id'],
                          tuple(body['constraints']))
phase, reply = sys.argv[4], decode(sys.argv[5])
c = OfflineC({'instruction.md': AGENTS})
c.evidence.update(request.evidence_refs)
existing = (ExistingExperience(scope, snapshot.snapshot_sha256, 'python-tests', 'rule',
                               'instruction.md', OLD, 'rule-version-1'),)

def ask(prompt):
    print('scripted callback', flush=True)
    return reply

service = ReflectionService(LearningStore(Path(sys.argv[2])), c, c, ask=ask,
                            read_existing=lambda *_: existing)
method = 'claim' if phase == 'after_claim' else 'save_draft'
original = getattr(service.reflections, method)

def interrupt(*args):
    if phase != 'before_draft_commit':
        original(*args)
    os._exit(37)

setattr(service.reflections, method, interrupt)
receipt = service.submit(request)
assert receipt.decision.reason_code == 'reflection_in_progress'
sys.exit(3)
"""
    checkout = Path(__file__).resolve().parents[2]
    command = [sys.executable, '-B', '-X', 'utf8', '-c', script,
               str(checkout), str(fixture.store.path), encode(asdict(request)), phase, encode(fixture.reply)]
    if phase == 'after_claim':
        with service.reflections.lock(request):
            denied = subprocess.run(command, capture_output=True, text=True, timeout=10)
            assert denied.returncode == 3, denied.stderr
            assert not denied.stdout and service.reflections.read(request) is None
    interrupted = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert interrupted.returncode == 37, interrupted.stderr
    callback_ran = phase != 'after_claim'
    assert interrupted.stdout.splitlines() == (['scripted callback'] if callback_ran else [])
    saved = service.reflections.read(request)
    assert saved.call_started is callback_ran
    assert (saved.draft is not None) == (phase == 'after_draft_commit')
    assert saved.candidate is None and saved.job is None

    receipt = fixture.service(reopen=True).submit(request)
    assert fixture.service(reopen=True).submit(request) == receipt
    assert len(fixture.prompts) + int(callback_ran) == 1
    if phase == 'before_draft_commit':
        assert receipt.decision.reason_code == 'reflection_outcome_unknown' and receipt.job is None
        assert fixture.service().reflections.read(request).draft.reason_code == 'reflection_outcome_unknown'
        assert not fixture.c.submissions and not fixture.c.registrations
    else:
        assert receipt.job == JobRef(*SCOPE.key(), 'job-fixture')
        assert fixture.c.registrations == len(fixture.c.submissions) == len(fixture.c.jobs) == 1


@pytest.mark.parametrize('case,reason', [('one_off', 'evidence_not_triggered'), ('changed', 'source_conflict')])
def test_d1_audit_evidence_cannot_trigger_d2(tmp_path, case, reason):
    items = ((source_item('one', kind='one_off'),) if case == 'one_off' else
             (source_item('same'), source_item('same', 'changed body')))
    fixture = setup(tmp_path, items=items)
    ref = fixture.source.receipts[0].evidence_ref
    result = fixture.service().submit(fixture.request((ref,)))
    assert result.decision.reason_code == reason
    assert not fixture.prompts and not fixture.c.artifacts


def test_conflicting_quotes_allow_explicit_deferral_and_no_submission(tmp_path):
    fixture = setup(tmp_path, items=(source_item('one', 'Run tests for Python edits.'),
                                    source_item('two', 'Do not run tests for Python edits.')))
    fixture.reply.clear()
    fixture.reply.update(decision='deferred', reason_code='conflicting_evidence')
    receipt = fixture.service().submit(fixture.request())
    assert receipt.decision.status == 'deferred' and receipt.decision.reason_code == 'conflicting_evidence'
    assert len(fixture.prompts[0]['evidence']) == 2 and not fixture.c.submissions


def test_evidence_and_catalog_are_scoped_before_callback(tmp_path):
    fixture = setup(tmp_path)
    other = LearningScope(SCOPE.profile_id, 'other-project')
    fixture.store.update_learning_policy(replace(fixture.update, scope=other), lambda _: True)
    cross_project = fixture.service().submit(replace(fixture.request(), scope=other))
    assert cross_project.decision.reason_code == 'evidence_not_triggered'
    catalog = (replace(fixture.existing[0], scope=other),)
    cross_catalog = fixture.service(catalog=catalog).submit(fixture.request())
    assert cross_catalog.decision.reason_code == 'out_of_scope'
    assert not fixture.prompts and not fixture.c.submissions
    assert fixture.service().submit(fixture.request()).job is not None


@pytest.mark.parametrize('other', [LearningScope('other-profile', SCOPE.project_id),
                                 LearningScope(SCOPE.profile_id, 'other-project')])
def test_mixed_scope_evidence_is_refused_before_reflection(tmp_path, other):
    fixture = setup(tmp_path)
    item = source_item('selected', 'This evidence belongs to a different scope.')
    grant = replace(fixture.update, scope=other)
    foreign = LearningService(fixture.store, SelectedImportSource(other, SOURCE, (item,)),
                              confirm_policy=lambda request: request == grant)
    foreign.update_learning_policy(grant)
    poll = foreign.poll_source(other, SOURCE)
    (foreign_ref,) = poll.receipts[0].decision.evidence_refs
    local = fixture.request()

    mixed = fixture.service().submit(fixture.request((*local.evidence_refs, foreign_ref)))
    assert mixed.decision.reason_code == 'evidence_not_triggered'
    foreign_snapshot = fixture.service().submit(replace(fixture.request((foreign_ref,)), scope=other))
    assert foreign_snapshot.decision.reason_code == 'out_of_scope'
    assert not fixture.prompts and not fixture.c.submissions
    assert fixture.service().submit(local).job is not None
    assert [item['ref'] for item in fixture.prompts[0]['evidence']] == list(local.evidence_refs)

def test_selected_files_and_existing_topic_cannot_be_redirected(tmp_path):
    fixture = setup(tmp_path, files={'instruction.md': AGENTS, SKILL: SKILL_PREFIX + OLD})
    fixture.reply.update(path=SKILL, kind='skill')
    assert fixture.service().submit(fixture.request()).decision.reason_code == 'existing_experience_conflict'
    assert not fixture.c.artifacts


def test_skill_frontmatter_cannot_be_used_as_an_edit_anchor(tmp_path):
    fixture = setup(tmp_path, files={SKILL: SKILL_PREFIX + OLD}, path=SKILL, kind='skill')
    catalog = (replace(fixture.existing[0], text='description: Verify changed files.'),)
    rejected = fixture.service(catalog=catalog).submit(fixture.request())
    assert rejected.decision.reason_code == 'skill_frontmatter_changed'
    assert not fixture.c.artifacts


def test_snapshot_is_checked_before_reserving_evidence(tmp_path):
    fixture = setup(tmp_path)
    good = fixture.request()
    stale = replace(good, snapshot=replace(good.snapshot, snapshot_sha256='f' * 64))
    assert fixture.service().submit(stale).decision.reason_code == 'stale_snapshot'
    assert not fixture.prompts and not fixture.c.artifacts
    assert fixture.service().submit(good).job is not None


def test_existing_model_session_accepts_the_offline_reflection_callback(tmp_path):
    fixture = setup(tmp_path)
    baseline_store = EventStore(tmp_path / 'personal.sqlite3')
    project = baseline_store.add_project(tmp_path)
    settings = load_model_settings({
        'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1',
        'HG_METHOD_MODEL': 'offline-reflection',
        'HG_METHOD_API_KEY': 'fixture-credential-never-sent',
    })
    client = ScriptedModelClient([fixture.reply])
    model = ModelSession(baseline_store, project['id'], settings, fixture=True, client=client)
    with model:
        receipt = fixture.service(callback=model.for_stage('reflect', system_prompt=SYSTEM)).submit(fixture.request())
    assert receipt.job == JobRef(*SCOPE.key(), 'job-fixture')
    assert len(client.requests) == 1 and model.usage()['transport_attempts'] == 0


def test_revocation_after_preparation_prevents_queue_submission(tmp_path):
    fixture = setup(tmp_path)
    service = fixture.service()
    request = fixture.request()
    assert service.reflect(request).status == 'candidate_ready'
    fixture.store.update_learning_policy(replace(
        fixture.update, expected_revision=1, idempotency_key='revoke', source_ids=(), confirmation_ref=None,
    ), None)
    outcome = service.submit(request)
    assert outcome.decision.reason_code == 'source_revoked'
    assert not fixture.c.jobs and len(fixture.prompts) == 1


def test_reflection_reservation_prevents_concurrent_and_overlapping_work(tmp_path):
    fixture = setup(tmp_path, items=(source_item('one'), source_item('two')))
    both = fixture.request()
    first = fixture.request((both.evidence_refs[0],))
    started, resume = Event(), Event()

    def ask(prompt):
        started.set()
        assert resume.wait(5)
        return fixture.reply

    with ThreadPoolExecutor(max_workers=1) as worker:
        task = worker.submit(fixture.service(callback=ask).submit, first)
        try:
            assert started.wait(5)
            assert fixture.service().submit(first).decision.reason_code == 'reflection_in_progress'
            assert fixture.service().submit(both).decision.reason_code == 'reflection_input_conflict'
        finally:
            resume.set()
        assert task.result().job == JobRef(*SCOPE.key(), 'job-fixture')
    assert not fixture.prompts and len(fixture.c.jobs) == 1


def test_learning_schema_upgrade_preserves_d1_state(tmp_path):
    fixture = setup(tmp_path)
    prior = fixture.store.read_receipts(SCOPE)
    with closing(fixture.store.connection()) as db, db:
        db.execute('DROP TABLE learning_trials')
        db.execute('DROP TABLE learning_operations')
        db.execute('DROP TABLE maintenance_decisions')
        db.execute('DROP TABLE learning_revisions')
        db.execute('DROP TABLE reflection_evidence')
        db.execute('DROP TABLE reflections')
        db.execute('PRAGMA user_version=1')
    reopened = LearningStore(fixture.store.path)
    assert reopened.read_receipts(SCOPE) == prior
    assert reopened.read_learning_policy(SCOPE).source_ids == (SOURCE,)
    with closing(reopened.connection()) as db:
        assert db.execute('SELECT count(*) FROM learning_trials').fetchone()[0] == 0
    assert fixture.service(reopen=True).submit(fixture.request()).job is not None


def test_v4_upgrade_preserves_receipts_and_does_not_retry_an_ambiguous_claim(tmp_path):
    fixture = setup(tmp_path, items=(source_item('one'), source_item('two')))
    first, second = fixture.request().evidence_refs
    completed, pending = fixture.request((first,)), fixture.request((second,))
    service = fixture.service()
    receipt = service.submit(completed)
    assert service.reflections.claim(pending)
    with closing(fixture.store.connection()) as db, db:
        db.execute('ALTER TABLE reflections DROP COLUMN call_started')
        db.execute('PRAGMA user_version=4')
    reopened = fixture.service(reopen=True)
    assert reopened.submit(completed) == receipt
    assert reopened.submit(pending).decision.reason_code == 'reflection_outcome_unknown'
    evidence, = reopened.store.read_reflection_evidence(SCOPE, pending.evidence_refs)
    assert evidence['ref'] == second
    assert len(fixture.prompts) == len(fixture.c.submissions) == 1


@pytest.mark.parametrize('version', [5, 6])
def test_upgrade_preserves_previous_trial_records(tmp_path, version):
    fixture = setup(tmp_path)
    request = fixture.request()
    receipt = fixture.service().submit(request)
    with closing(fixture.store.connection()) as db, db:
        db.execute('INSERT INTO learning_trials VALUES (?, ?, ?, ?, ?, ?)',
                   (*SCOPE.key(), 'prior-trial', 'trial-request', digest('trial-request'), '{"fixture":true}'))
        db.execute(f'PRAGMA user_version={version}')

    reopened = fixture.service(reopen=True)
    assert reopened.submit(request) == receipt
    with closing(reopened.store.connection()) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 7
        saved = db.execute('SELECT body FROM learning_trials WHERE id=?', ('prior-trial',)).fetchone()
        assert saved['body'] == '{"fixture":true}'
