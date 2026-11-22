"""D1 end-to-end checks with explicitly selected synthetic events."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
from threading import Barrier

import pytest

import harnessgrad.personal.learning.service as learning_service
from harnessgrad.personal.contracts import digest, source_events
from harnessgrad.personal.evidence import EvidenceSelection
from harnessgrad.personal.fixtures import example
from harnessgrad.personal.learning.contracts import (
    LearningPolicy,
    LearningPolicyUpdate,
    LearningScope,
    SourceCapability,
    SourceItem,
)
from harnessgrad.personal.learning.service import LearningService
from harnessgrad.personal.learning.source import SelectedImportSource
from harnessgrad.personal.learning.store import LearningStore, PolicyConflict

SCOPE = LearningScope('fixture-profile', 'fixture-project')
SOURCE = 'public-fixture-v1'
NOW = datetime(2026, 11, 18, 8, tzinfo=timezone.utc)


@pytest.fixture
def item():
    def make(event_id, *, text=None, session_id=None, truncated=False, role='user',
             kind='reusable', boundary=True, own=False):
        # Normalize with the same import contract as B2 before tagging the fixture task boundary.
        fixture = example()
        value = {**fixture['events'][2], 'event_id': event_id, 'role': role, 'truncated': truncated}
        if text is not None:
            value['text'] = text
        if session_id is not None:
            value['session_id'] = session_id
        (event,) = source_events({**fixture, 'events': [value]})
        selection = EvidenceSelection(SOURCE, event.session_id, event_id, digest(value), 1, len(event.text))
        return SourceItem(event, selection, kind, boundary, own)
    return make


def configure(*, scope=SCOPE, cooldown=0, budget=10):
    return LearningPolicyUpdate(scope, 0, True, (SOURCE,), cooldown, budget, 'enable', 'fixture-confirm')


def learning(tmp_path, items, *, scope=SCOPE, cooldown=0, budget=10, source=None):
    request = configure(scope=scope, cooldown=cooldown, budget=budget)
    source = source if source is not None else SelectedImportSource(scope, SOURCE, items)
    service = LearningService(
        LearningStore(tmp_path / 'learning.sqlite3'), source, confirm_policy=lambda submitted: submitted == request,
    )
    service.update_learning_policy(request)
    return service


def test_opt_in_scope_and_policy_replay(tmp_path, item):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    source = SelectedImportSource(SCOPE, SOURCE, (item('eligible'),))
    request = configure()
    service = LearningService(store, source, confirm_policy=lambda submitted: submitted == request)

    assert service.read_learning_policy(SCOPE) == LearningPolicy(SCOPE)
    assert service.poll_source(SCOPE, SOURCE, now=NOW).reason_code == 'learning_disabled'
    assert store.read_receipts(SCOPE) == ()
    assert service.probe_source(SCOPE, SOURCE).status == 'ready'
    with pytest.raises(PermissionError):
        service.update_learning_policy(replace(request, confirmation_ref=None))
    with pytest.raises(PermissionError):
        service.update_learning_policy(replace(request, source_ids=(SOURCE, 'unconfirmed')))
    enabled = service.update_learning_policy(request)
    assert enabled.revision == 1 and enabled.enabled
    assert service.update_learning_policy(request) == enabled
    with pytest.raises(PolicyConflict, match='request key'):
        service.update_learning_policy(replace(request, max_reflections_per_day=1))
    with pytest.raises(PolicyConflict, match='revision'):
        service.update_learning_policy(replace(request, idempotency_key='other'))

    poll = service.poll_source(SCOPE, SOURCE, now=NOW)
    (receipt,) = poll.receipts
    (ref,) = receipt.decision.evidence_refs
    assert receipt.decision.status == 'triggered'
    for other in (LearningScope('other-profile', SCOPE.project_id), LearningScope(SCOPE.profile_id, 'other-project')):
        assert service.probe_source(other, SOURCE).status == 'refused'
        assert service.poll_source(other, SOURCE, now=NOW).reason_code == 'learning_disabled'
        assert store.read_receipts(other) == ()
        with pytest.raises(PermissionError):
            store.read_evidence(other, ref)


def test_confirmation_is_single_use_except_for_request_replay(tmp_path, item):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    source = SelectedImportSource(SCOPE, SOURCE, (item('one'),))
    service = LearningService(store, source, confirm_policy=lambda request: True)
    request = configure()
    enabled = service.update_learning_policy(request)
    service.update_learning_policy(replace(
        request, enabled=False, expected_revision=1, confirmation_ref=None, idempotency_key='disable',
    ))

    assert service.update_learning_policy(request) == enabled
    assert not service.read_learning_policy(SCOPE).enabled
    with pytest.raises(PermissionError, match='already used'):
        service.update_learning_policy(replace(request, expected_revision=2, idempotency_key='reenable'))


def test_selected_evidence_dedup_and_restart(tmp_path, item):
    eligible = item('reuse', text='😀Run tests after Python edits.')
    deliveries = (
        item('once', kind='one_off'),
        item('own', own=True),
        item('still-running', boundary=False),
        item('model-origin', role='assistant'),
        item('whitespace', text='  '),
        item('truncated', truncated=True),
        eligible,
        eligible,
        item('reuse', session_id='another-session'),
    )
    service = learning(tmp_path, deliveries)
    first = service.poll_source(SCOPE, SOURCE, limit=7, now=NOW)
    assert first.has_more and first.cursor
    assert [row.decision.reason_code for row in first.receipts] == [
        'one_off_request', 'own_event', 'not_task_boundary', 'missing_evidence', 'missing_evidence',
        'partial_evidence', None,
    ]
    assert [row.decision.status for row in first.receipts] == ['skipped'] * 6 + ['triggered']
    assert first.receipts[1].evidence_ref is None  # Own events do not retain text.
    assert not any(row.decision.evidence_refs for row in first.receipts[:-1])
    (ref,) = first.receipts[-1].decision.evidence_refs
    evidence = service.store.read_evidence(SCOPE, ref)
    assert evidence['text'] == 'Run tests after Python edits.'
    assert evidence['partial'] and not evidence['context_incomplete']
    assert evidence['provenance'] == 'user_quote'
    assert evidence['sha256'] == eligible.event_sha256

    reopened = LearningService(LearningStore(service.store.path), SelectedImportSource(SCOPE, SOURCE, deliveries))
    second = reopened.poll_source(SCOPE, SOURCE, now=NOW)
    assert second.cursor != first.cursor and second.duplicates == 1 and not second.has_more
    assert [row.session_id for row in second.receipts] == ['another-session']
    assert reopened.store.read_receipts(SCOPE, after=first.receipts[-1].seq) == second.receipts
    empty = reopened.poll_source(SCOPE, SOURCE, now=NOW)
    assert empty.cursor == second.cursor and empty.receipts == () and empty.duplicates == 0


@pytest.mark.parametrize('legacy_receipt', [False, True])
def test_reclassified_feedback_triggers_once_after_restart(tmp_path, item, legacy_receipt):
    unclassified = item('unclassified', kind='unspecified')
    other = item('other')
    initial = learning(tmp_path, (unclassified, other), cooldown=60)
    first, following = initial.poll_source(SCOPE, SOURCE, now=NOW).receipts
    assert (first.decision.status, first.decision.reason_code) == ('skipped', 'unclassified_feedback')
    assert following.decision.status == 'triggered'
    if legacy_receipt:
        with initial.store.transaction() as db:
            db.execute("UPDATE decisions SET reason='missing_evidence' WHERE seq=?", (first.seq,))

    classified = replace(unclassified, request_kind='reusable')
    source = SelectedImportSource(SCOPE, SOURCE, (unclassified, other, classified, classified))
    reopened = LearningService(LearningStore(initial.store.path), source)
    pending = reopened.poll_source(SCOPE, SOURCE, now=NOW)
    assert pending.reason_code == 'cooldown'
    result = reopened.poll_source(SCOPE, SOURCE, now=NOW + timedelta(minutes=1))

    (receipt,) = result.receipts
    assert result.duplicates == 1
    assert receipt.event_sha256 == first.event_sha256
    assert (receipt.decision.status, receipt.decision.evidence_refs) == ('triggered', (first.evidence_ref,))
    assert reopened.store.read_reflection_evidence(SCOPE, (first.evidence_ref,))
    assert not reopened.poll_source(SCOPE, SOURCE, now=NOW + timedelta(minutes=1)).receipts


def test_classification_preserves_final_decisions_and_selection(tmp_path, item):
    unclassified = item('unclassified', text=' before after', kind='unspecified')
    one_off = item('one-off', kind='one_off')
    reselected = replace(unclassified, request_kind='reusable', selection=replace(unclassified.selection, start=8))
    repeat = replace(one_off, request_kind='reusable')
    service = learning(tmp_path, (unclassified, one_off, reselected, repeat))

    result = service.poll_source(SCOPE, SOURCE, now=NOW)

    assert [row.decision.reason_code for row in result.receipts] == [
        'unclassified_feedback', 'one_off_request', 'selection_changed',
    ]
    assert result.duplicates == 1
    assert not any(row.decision.evidence_refs for row in result.receipts)


def test_classification_can_supply_the_first_selected_excerpt(tmp_path, item):
    classified = item('feedback', text=' Run focused tests after Python edits.')
    unclassified = replace(classified, request_kind='unspecified', selection=None)
    service = learning(tmp_path, (unclassified, classified))

    first, second = service.poll_source(SCOPE, SOURCE, now=NOW).receipts

    assert first.decision.reason_code == 'unclassified_feedback' and first.evidence_ref is None
    (ref,) = second.decision.evidence_refs
    assert service.store.read_reflection_evidence(SCOPE, (ref,))[0]['text'] == 'Run focused tests after Python edits.'


def test_conflicting_delivery_preserves_both_hashes_and_cursor(tmp_path, item):
    original = item('same', text=' first selected excerpt')
    conflict = item('same', text=' conflicting selected excerpt')
    service = learning(tmp_path, (original, original, conflict, item('after-conflict')))
    first = service.poll_source(SCOPE, SOURCE, limit=1, now=NOW)
    second = service.poll_source(SCOPE, SOURCE, now=NOW)

    assert second.reason_code == 'source_conflict' and second.cursor == first.cursor
    assert second.duplicates == 1
    persisted = service.store.read_receipts(SCOPE)
    assert [row.event_sha256 for row in persisted] == [original.event_sha256, conflict.event_sha256]
    assert [row.decision.status for row in persisted] == ['triggered', 'deferred']
    assert not persisted[1].decision.evidence_refs
    assert [
        service.store.read_evidence(SCOPE, row.evidence_ref)['text'] for row in persisted
    ] == ['first selected excerpt', 'conflicting selected excerpt']
    replay = service.poll_source(SCOPE, SOURCE, now=NOW)
    assert replay.reason_code == 'source_conflict' and not replay.receipts and replay.cursor == first.cursor
    assert service.store.read_receipts(SCOPE) == persisted


def test_conflict_retains_selected_evidence_from_skipped_deliveries(tmp_path, item):
    original = item('same', text=' original one-off request', kind='one_off')
    altered = item('same', text=' edited one-off request', kind='one_off')
    service = learning(tmp_path, (original, altered))
    poll = service.poll_source(SCOPE, SOURCE, now=NOW)

    assert poll.reason_code == 'source_conflict' and poll.cursor is None
    assert [row.decision.status for row in poll.receipts] == ['skipped', 'deferred']
    assert not any(row.decision.evidence_refs for row in poll.receipts)
    assert [
        service.store.read_evidence(SCOPE, row.evidence_ref)['text'] for row in poll.receipts
    ] == ['original one-off request', 'edited one-off request']


@pytest.mark.parametrize('change, reason', [('withdrawn', 'missing_evidence'), ('reselected', 'selection_changed')])
def test_deferred_event_rechecks_current_evidence_selection(tmp_path, item, change, reason):
    first = item('first')
    pending = item('pending', text=' before after')
    service = learning(tmp_path, (first, pending), cooldown=60)
    poll = service.poll_source(SCOPE, SOURCE, now=NOW)
    assert poll.reason_code == 'cooldown'
    (old_ref,) = poll.receipts[-1].decision.evidence_refs

    selection = replace(pending.selection, start=8) if change == 'reselected' else None
    source = SelectedImportSource(SCOPE, SOURCE, (first, replace(pending, selection=selection)))
    reopened = LearningService(LearningStore(service.store.path), source)
    result = reopened.poll_source(SCOPE, SOURCE, now=NOW + timedelta(minutes=1))

    assert result.cursor and result.duplicates == 1
    (receipt,) = result.receipts
    assert (receipt.decision.status, receipt.decision.reason_code) == ('skipped', reason)
    assert not receipt.decision.evidence_refs
    assert reopened.store.read_evidence(SCOPE, old_ref)['text'] == 'before after'


def test_source_rewrite_does_not_skip_past_checkpoint(tmp_path, item):
    first = item('first')
    second = item('second')
    service = learning(tmp_path, (first,))
    original = service.poll_source(SCOPE, SOURCE, now=NOW)

    modified = item('first', text=' changed source content')
    replaced = SelectedImportSource(SCOPE, SOURCE, (modified, second))
    reopened = LearningService(LearningStore(service.store.path), replaced)
    stalled = reopened.poll_source(SCOPE, SOURCE, now=NOW)
    assert stalled.cursor == original.cursor and stalled.reason_code == 'source_conflict'
    assert reopened.store.read_receipts(SCOPE) == original.receipts

    appended = SelectedImportSource(SCOPE, SOURCE, (first, second))
    clean = LearningService(LearningStore(service.store.path), appended).poll_source(SCOPE, SOURCE, now=NOW)
    assert clean.cursor != original.cursor
    assert [row.event_id for row in clean.receipts] == ['second']


def test_cooldown_and_utc_daily_budget_preserve_deferred_work(tmp_path, item):
    service = learning(tmp_path, (item('one'), item('two'), item('three')), cooldown=60, budget=2)
    day_end = NOW.replace(hour=23, minute=57)
    first = service.poll_source(SCOPE, SOURCE, now=day_end)
    assert first.cursor is None and first.reason_code == 'cooldown'
    assert [row.decision.status for row in first.receipts] == ['triggered', 'deferred']
    within_cooldown = service.poll_source(SCOPE, SOURCE, now=day_end + timedelta(seconds=59))
    assert within_cooldown.cursor is None and within_cooldown.receipts == ()

    second = service.poll_source(SCOPE, SOURCE, now=day_end + timedelta(minutes=1))
    assert second.cursor is None and second.reason_code == 'budget_exhausted'
    assert [row.decision.status for row in second.receipts] == ['triggered', 'deferred']
    assert second.duplicates == 1
    assert first.receipts[-1].decision.evidence_refs == second.receipts[0].decision.evidence_refs

    next_day = service.poll_source(SCOPE, SOURCE, now=day_end + timedelta(minutes=3))
    assert next_day.cursor and next_day.duplicates == 2 and next_day.reason_code is None
    assert [row.event_id for row in service.store.read_receipts(SCOPE) if row.decision.status == 'triggered'] == [
        'one', 'two', 'three',
    ]


def test_daily_budget_uses_decision_time_when_read_crosses_midnight(tmp_path, item, monkeypatch):
    before_midnight = NOW.replace(hour=23, minute=59, second=59)
    after_midnight = before_midnight + timedelta(seconds=1)

    class Clock(datetime):
        current = before_midnight

        @classmethod
        def now(cls, tz=None):
            return cls.current

    class MidnightSource(SelectedImportSource):
        def read_source(self, scope, source_id, *, cursor, limit=50):
            page = super().read_source(scope, source_id, cursor=cursor, limit=limit)
            Clock.current = after_midnight
            return page

    monkeypatch.setattr(learning_service, 'datetime', Clock)
    a, b, c = (item(name) for name in ('a', 'b', 'c'))
    service = learning(tmp_path, (a,), budget=1)
    assert service.poll_source(SCOPE, SOURCE, now=before_midnight).receipts[0].decision.status == 'triggered'
    reopened = LearningService(service.store, MidnightSource(SCOPE, SOURCE, (a, b, c)))
    poll = reopened.poll_source(SCOPE, SOURCE)

    assert [(row.event_id, row.decision.status) for row in poll.receipts] == [('b', 'triggered'), ('c', 'deferred')]
    assert poll.reason_code == 'budget_exhausted'


def test_revocation_during_read_drops_page_and_preserves_checkpoint(tmp_path, item):
    class RevokingSource(SelectedImportSource):
        def read_source(self, scope, source_id, *, cursor, limit=50):
            page = super().read_source(scope, source_id, cursor=cursor, limit=limit)
            if cursor:
                request = replace(configure(), expected_revision=1, source_ids=(),
                                  confirmation_ref=None, idempotency_key='revoke')
                service.update_learning_policy(request)
            return page

    source = RevokingSource(SCOPE, SOURCE, (item('allowed'), item('revoked-during-read')))
    service = learning(tmp_path, (), source=source)
    first = service.poll_source(SCOPE, SOURCE, limit=1, now=NOW)
    second = service.poll_source(SCOPE, SOURCE, now=NOW)
    assert second.reason_code == 'source_revoked' and second.cursor == first.cursor
    assert service.store.read_receipts(SCOPE) == first.receipts
    assert service.poll_source(SCOPE, SOURCE, now=NOW) == second
    with pytest.raises(PermissionError):
        service.store.read_evidence(SCOPE, first.receipts[0].decision.evidence_refs[0])


def test_revocation_during_probe_prevents_read(tmp_path, item):
    class RevokingSource(SelectedImportSource):
        def probe_source(self, scope, source_id):
            request = replace(configure(), expected_revision=1, source_ids=(),
                              confirmation_ref=None, idempotency_key='revoked')
            service.update_learning_policy(request)
            return super().probe_source(scope, source_id)

        def read_source(self, scope, source_id, *, cursor, limit=50):
            pytest.fail('revoked source was read')

    source = RevokingSource(SCOPE, SOURCE, (item('one'),))
    service = learning(tmp_path, (), source=source)
    result = service.poll_source(SCOPE, SOURCE, now=NOW)
    assert result.reason_code == 'source_revoked' and result.cursor is None
    assert not service.store.read_receipts(SCOPE)


def test_unavailable_source_is_never_read(tmp_path):
    class UnavailableSource:
        def probe_source(self, scope, source_id):
            return SourceCapability(source_id, 'selected_import', 'unavailable', 'source_unavailable')

        def read_source(self, scope, source_id, *, cursor, limit=50):
            pytest.fail('an unavailable adapter was read')

    service = learning(tmp_path, (), source=UnavailableSource())
    result = service.poll_source(SCOPE, SOURCE, now=NOW)
    assert result.reason_code == 'source_unavailable' and result.cursor is None
    assert not result.receipts and not service.store.read_receipts(SCOPE)


def test_concurrent_polls_record_only_one_trigger(tmp_path, item):
    gate = Barrier(2)

    class SynchronizedSource(SelectedImportSource):
        def read_source(self, scope, source_id, *, cursor, limit=50):
            gate.wait(timeout=5)
            return super().read_source(scope, source_id, cursor=cursor, limit=limit)

    source = SynchronizedSource(SCOPE, SOURCE, (item('one'),))
    service = learning(tmp_path, (), source=source)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(lambda _: service.poll_source(SCOPE, SOURCE, now=NOW), range(2))
    assert {first.reason_code, second.reason_code} == {None, 'checkpoint_changed'}
    assert first.cursor == second.cursor
    assert [row.decision.status for row in service.store.read_receipts(SCOPE)] == ['triggered']


def test_checkpoint_and_receipt_rollback_together(tmp_path, item, monkeypatch):
    service = learning(tmp_path, (item('one'),))
    with monkeypatch.context() as patch:
        def fail_checkpoint(db, scope, source_id, cursor):
            raise RuntimeError('simulated storage failure')

        patch.setattr(service.store, 'save_checkpoint', fail_checkpoint)
        with pytest.raises(RuntimeError, match='storage failure'):
            service.poll_source(SCOPE, SOURCE, now=NOW)

    assert not service.store.read_receipts(SCOPE)
    assert service.poll_source(SCOPE, SOURCE, now=NOW).receipts[0].decision.status == 'triggered'


def test_store_refuses_unrelated_database(tmp_path):
    path = tmp_path / 'unrelated.sqlite3'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE preserved (value TEXT)')

    with pytest.raises(ValueError, match='not a recognized learning database'):
        LearningStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT name FROM sqlite_master').fetchall() == [('preserved',)]


def test_source_cursors_are_scoped_and_empty_page_retains_position(item):
    source = SelectedImportSource(SCOPE, SOURCE, (item('one'),))
    page = source.read_source(SCOPE, SOURCE, cursor=None, limit=1)
    empty = source.read_source(SCOPE, SOURCE, cursor=page.next_cursor, limit=1)
    assert empty.next_cursor == page.next_cursor and empty.events == () and not empty.has_more
    other = LearningScope(SCOPE.profile_id, 'different-project')
    other_source = SelectedImportSource(other, SOURCE, (item('one'),))
    with pytest.raises(ValueError, match='invalid cursor'):
        other_source.read_source(other, SOURCE, cursor=page.next_cursor)
    with pytest.raises(ValueError, match='limit'):
        source.read_source(SCOPE, SOURCE, cursor=None, limit=True)
