"""D2 receipts through D3 maintenance and a scoped, in-memory C version port."""
from contextlib import closing
from dataclasses import replace

import pytest

from harnessgrad.personal.contracts import decode, digest, encode, source_events
from harnessgrad.personal.evidence import EvidenceSelection
from harnessgrad.personal.fixtures import example
from harnessgrad.personal.learning.contracts import LearningPolicyUpdate, LearningScope, SourceItem
from harnessgrad.personal.learning.maintenance import MaintenanceService
from harnessgrad.personal.learning.maintenance_contracts import (
    ItemRevision, MaintenanceRequest, OperationRef, TaskContext,
)
from harnessgrad.personal.learning.reflection import ReflectionService
from harnessgrad.personal.learning.reflection_contracts import (
    PortRejected, ReflectionInput,
)
from harnessgrad.personal.learning.service import LearningService
from harnessgrad.personal.learning.source import SelectedImportSource
from harnessgrad.personal.learning.store import LearningStore
from personal_maintenance_fixtures import BEFORE, PATH, SCOPE, TARGET, FixtureC

OTHER = LearningScope('profile', 'other-project')
RULE = '- Run related tests after editing Python.'
CORRECTED = '- Run related tests after editing Python and report the results.'
CONDITIONS = ('python-edit',)
TASK = TaskContext((TARGET,), CONDITIONS)


@pytest.fixture
def lane(tmp_path):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    source = example()
    events = source_events({**source, 'events': [
        {**source['events'][2], 'event_id': f'exp-{index}'} for index in range(2)
    ]})
    selected = tuple(SourceItem(
        event, EvidenceSelection(event.source_instance, event.session_id, event.event_id,
                                 digest(event.as_dict()), 0, len(event.text)),
        'reusable', task_boundary=True,
    ) for event in events)
    source_id = events[0].source_instance
    grant = LearningPolicyUpdate(SCOPE, 0, True, (source_id,), 0, 8, 'enable', 'local-grant')
    intake = LearningService(store, SelectedImportSource(SCOPE, source_id, selected),
                             confirm_policy=lambda request: request == grant)
    intake.update_learning_policy(grant)
    evidence = intake.poll_source(SCOPE, source_id)
    c = FixtureC()
    reflection = ReflectionService(
        store, c, c, read_existing=lambda scope, snapshot: (),
        ask=lambda _: {'decision': 'propose', 'kind': 'rule', 'topic_id': 'python-tests', 'path': PATH, 'text': RULE,
                       'conditions': list(CONDITIONS), 'criterion': 'Observe the report of the related test run.'},
    )
    snapshot = c.freeze_snapshot(SCOPE, (TARGET,))
    requests = tuple(ReflectionInput(SCOPE, receipt.decision.evidence_refs, snapshot, 'python-tests')
                     for receipt in evidence.receipts)
    return store, c, reflection, requests


def start(lane, *, count=1):
    store, c, reflection, requests = lane
    service = MaintenanceService(store, c, c, c)
    proposed = []
    for request in requests[:count]:
        result = reflection.submit(request)
        item = service.track_reflection(request)
        assert item.state == 'proposed' and item.job == result.job
        assert service.track_reflection(request) == item
        proposed.append(item)
    assert not service.select_context(SCOPE, TASK)
    active = tuple(service.reconcile_learning_item(SCOPE, item.item_id, c.apply(item.job)) for item in proposed)
    return service, active


def maintenance(action, *items, key=None, text=None):
    return MaintenanceRequest(SCOPE, action, tuple(ItemRevision(item.item_id, item.revision) for item in items),
                              f'Test {action}', key or action, text)


def adopt_second_rule(lane, service, text):
    store, c, _, (_, request) = lane
    request = replace(request, snapshot=c.freeze_snapshot(SCOPE, (TARGET,)))
    reply = {'decision': 'propose', 'kind': 'rule', 'topic_id': 'second-rule', 'path': PATH, 'text': text,
             'conditions': list(CONDITIONS), 'criterion': 'Observe the second rule.'}
    reflection = ReflectionService(store, c, c, read_existing=lambda scope, snapshot: (), ask=lambda _: reply)
    result = reflection.submit(request)
    item = service.track_reflection(request)
    operation = c.apply(result.job)
    return service.reconcile_learning_item(SCOPE, item.item_id, operation), operation


def test_same_file_rules_stay_active_and_can_be_corrected(lane):
    store, c, _, _ = lane
    service, (first,) = start(lane)
    other_rule = '- List documentation changes separately in the completion report.'
    second, operation = adopt_second_rule(lane, service, other_rule)
    reopened = MaintenanceService(LearningStore(store.path), c, c, c)

    assert {item.summary for item in reopened.select_context(SCOPE, TASK)} == {RULE, other_rule}
    original, rebound = reopened.read_history(SCOPE, first.item_id)[-2:]
    assert original == first and rebound.version_receipt_ref == first.version_receipt_ref
    assert rebound.evidence_refs == first.evidence_refs
    assert reopened.reconcile_learning_item(SCOPE, second.item_id, operation) == second
    assert reopened.read_history(SCOPE, first.item_id)[-1] == rebound

    decision = reopened.request_maintenance(maintenance('correct', rebound, text=CORRECTED))
    queued = reopened.submit_candidate(SCOPE, decision.decision_id)
    reopened.reconcile_learning_item(SCOPE, first.item_id, c.apply(queued.job))

    assert {item.summary for item in reopened.select_context(SCOPE, TASK)} == {CORRECTED, other_rule}
    assert other_rule in c.files[PATH]


def test_retiring_a_rebound_rule_cannot_roll_back_the_other_rule(lane):
    _, c, _, _ = lane
    service, (first,) = start(lane)
    other_rule = '- List documentation changes separately in the completion report.'
    second, _ = adopt_second_rule(lane, service, other_rule)
    latest = service.read_history(SCOPE, first.item_id)[-1]

    retired = service.request_maintenance(maintenance('disable', latest))

    assert retired.status == 'recorded'
    assert service.select_context(SCOPE, TASK) == (second,)
    assert other_rule in c.files[PATH] and not c.rollback_calls


def test_rolling_back_the_latest_rule_keeps_the_earlier_rule_selected(lane):
    _, c, _, _ = lane
    service, (first,) = start(lane)
    other_rule = '- List documentation changes separately in the completion report.'
    second, _ = adopt_second_rule(lane, service, other_rule)
    decision = service.request_maintenance(maintenance('disable', second))
    c.grants.add('approved')
    operation = service.request_rollback(SCOPE, decision.decision_id, 'approved')
    c.finish_rollback(operation)
    service.reconcile_learning_item(SCOPE, second.item_id, operation)

    (selected,) = service.select_context(SCOPE, TASK)
    assert selected.item_id == first.item_id and selected.summary == RULE
    assert RULE in c.files[PATH] and other_rule not in c.files[PATH]


def test_replaying_old_operation_recovers_other_rules_binding(lane, monkeypatch):
    store, c, _, _ = lane
    service, (first,) = start(lane)
    other_rule = '- List documentation changes separately in the completion report.'
    # Older maintenance did not update other rules while recording the write.
    monkeypatch.setattr(service, '_carry_forward', lambda *_: ())
    second, operation = adopt_second_rule(lane, service, other_rule)
    reopened = MaintenanceService(LearningStore(store.path), c, c, c)
    assert reopened.select_context(SCOPE, TASK) == (second,)

    assert reopened.reconcile_learning_item(SCOPE, second.item_id, operation) == second
    assert {item.summary for item in reopened.select_context(SCOPE, TASK)} == {RULE, other_rule}
    older, newer = reopened.read_history(SCOPE, first.item_id)[-2:]
    assert older == first and newer.version_receipt_ref == first.version_receipt_ref


def test_disable_stops_new_context_and_preserves_history_and_evidence(lane):
    store, c, _, _ = lane
    service, (active,) = start(lane)
    prior_context = service.select_context(SCOPE, TASK)
    assert prior_context == (active,)
    assert service.select_context(SCOPE, TaskContext((TARGET,))) == ()
    assert service.select_context(OTHER, TASK) == ()
    original = store.read_evidence(SCOPE, active.evidence_refs[0])
    request = maintenance('disable', active)
    decision = service.request_maintenance(request)
    assert decision.status == 'needs_rollback' and not c.rollback_calls

    reopened = MaintenanceService(LearningStore(store.path), c, c, c)
    assert reopened.request_maintenance(request) == decision
    assert reopened.select_context(SCOPE, TASK) == ()
    assert prior_context == (active,)
    history = reopened.read_history(SCOPE, active.item_id)
    assert [item.state for item in history] == ['proposed', 'active', 'disabled']
    assert all(item.evidence_refs == active.evidence_refs for item in history)
    assert store.read_evidence(SCOPE, active.evidence_refs[0]) == original
    assert reopened.read_history(OTHER, active.item_id) == ()
    old_apply = OperationRef(active.job, f'apply-{active.job.job_id}')
    assert reopened.reconcile_learning_item(SCOPE, active.item_id, old_apply) == history[-1]


@pytest.mark.parametrize('action', ['disable', 'archive'])
def test_context_drops_an_item_retired_during_version_lookup(lane, monkeypatch, action):
    store, c, _, _ = lane
    service, (active,) = start(lane)
    evidence = store.read_evidence(SCOPE, active.evidence_refs[0])
    read_versions = c.read_current_versions

    def retire_during_read(scope, targets):
        service.request_maintenance(maintenance(action, active))
        return read_versions(scope, targets)

    with monkeypatch.context() as patch:
        patch.setattr(c, 'read_current_versions', retire_during_read)
        assert service.select_context(SCOPE, TASK) == ()
    assert service.select_context(SCOPE, TASK) == ()
    assert service.read_history(SCOPE, active.item_id)[-2] == active
    assert store.read_evidence(SCOPE, active.evidence_refs[0]) == evidence


def test_archive_restore_and_stale_expected_revision(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    archive = service.request_maintenance(maintenance('archive', active))
    assert archive.status == 'needs_rollback' and not service.select_context(SCOPE, TASK)
    archived = service.list_learning_items(SCOPE).items[0]
    assert archived.state == 'archived'
    stale = service.request_maintenance(maintenance('restore', active, key='stale'))
    assert stale.reason_code == 'revision_conflict'
    assert service.request_maintenance(maintenance('restore', archived)).status == 'recorded'
    (restored,) = service.select_context(SCOPE, TASK)
    assert restored.state == 'active' and restored.version_receipt_ref == active.version_receipt_ref
    with pytest.raises(PortRejected, match='revision_conflict'):
        service.request_rollback(SCOPE, archive.decision_id, 'stale-grant')
    assert not c.rollback_calls


def test_archiving_a_disabled_but_still_installed_item_keeps_rollback_available(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    disabled = service.request_maintenance(maintenance('disable', active))
    retired = service.list_learning_items(SCOPE).items[0]
    archive = service.request_maintenance(maintenance('archive', retired))
    assert archive.status == 'needs_rollback' and archive.restore_version_ref == disabled.restore_version_ref
    with pytest.raises(PortRejected, match='revision_conflict'):
        service.request_rollback(SCOPE, disabled.decision_id, 'approved')
    c.grants.add('approved')
    operation = service.request_rollback(SCOPE, archive.decision_id, 'approved')
    c.finish_rollback(operation)
    assert service.reconcile_learning_item(SCOPE, active.item_id, operation).state == 'archived'


def test_correction_replaces_only_after_verified_application(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    request = maintenance('correct', active, text=CORRECTED)
    decision = service.request_maintenance(request)
    assert decision.status == 'needs_candidate'
    assert service.select_context(SCOPE, TASK) == (active,)
    payload = c.candidates[decision.candidate.candidate_ref].payload
    assert payload['files'] == {PATH: c.files[PATH].replace(RULE, CORRECTED)}
    submitted = service.submit_candidate(SCOPE, decision.decision_id)
    assert service.submit_candidate(SCOPE, decision.decision_id) == submitted
    applied = c.apply(submitted.job)
    assert service.select_context(SCOPE, TASK) == ()
    corrected = service.reconcile_learning_item(SCOPE, active.item_id, applied)
    assert corrected.summary == CORRECTED and corrected.job != active.job
    assert service.select_context(SCOPE, TASK) == (corrected,)
    assert [item.summary for item in service.read_history(SCOPE, active.item_id)][-2:] == [RULE, CORRECTED]
    assert corrected.evidence_refs == active.evidence_refs


@pytest.mark.parametrize('action', ['disable', 'archive'])
def test_delayed_correction_receipt_does_not_reactivate_retired_item(lane, action):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    pending = service.request_maintenance(maintenance('correct', active, text=CORRECTED))
    job = service.submit_candidate(SCOPE, pending.decision_id).job
    service.request_maintenance(maintenance(action, active))
    history = service.read_history(SCOPE, active.item_id)
    op = c.apply(job)
    with pytest.raises(PortRejected, match='stale_operation'):
        service.reconcile_learning_item(SCOPE, active.item_id, op)
    assert service.read_history(SCOPE, active.item_id) == history
    assert not service.select_context(SCOPE, TASK)


def test_correction_receipt_must_start_at_the_reviewed_version(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    pending = service.request_maintenance(maintenance('correct', active, text=CORRECTED))
    job = service.submit_candidate(SCOPE, pending.decision_id).job
    prior = c.files[PATH]
    c.external_change('# Temporary edit\n')
    c.external_change(prior)
    operation = c.apply(job)
    with pytest.raises(PortRejected, match='version_conflict'):
        service.reconcile_learning_item(SCOPE, active.item_id, operation)
    assert service.read_history(SCOPE, active.item_id)[-1] == active
    assert not service.select_context(SCOPE, TASK)


def test_accepted_candidate_with_lost_ack_is_recovered_after_reopen(lane, monkeypatch):
    store, c, _, _ = lane
    service, (active,) = start(lane)
    decision = service.request_maintenance(maintenance('correct', active, text=CORRECTED))
    submit = c.submit_candidate

    def lost_ack(candidate):
        submit(candidate)
        raise ConnectionError('lost candidate acknowledgement')

    with monkeypatch.context() as patch:
        patch.setattr(c, 'submit_candidate', lost_ack)
        with pytest.raises(ConnectionError):
            service.submit_candidate(SCOPE, decision.decision_id)
    original = c.jobs[decision.decision_id][1]
    operation = c.apply(original)
    reopened = MaintenanceService(LearningStore(store.path), c, c, c)
    assert reopened.submit_candidate(SCOPE, decision.decision_id).job == original
    assert reopened.reconcile_learning_item(SCOPE, active.item_id, operation).summary == CORRECTED
    assert len(c.jobs) == 2


def test_merge_is_idempotent_and_retains_both_sources(lane):
    store, c, _, _ = lane
    service, (first, second) = start(lane, count=2)
    request = maintenance('merge', first, second)
    decision = service.request_maintenance(request)
    assert decision.status == 'recorded'
    reopened = MaintenanceService(LearningStore(store.path), c, c, c)
    assert reopened.request_maintenance(request) == decision
    (canonical,) = reopened.select_context(SCOPE, TASK)
    assert canonical.item_id == first.item_id and canonical.revision == first.revision + 1
    assert set(canonical.evidence_refs) == {*first.evidence_refs, *second.evidence_refs}
    archived = reopened.read_history(SCOPE, second.item_id)[-1]
    assert archived.state == 'archived' and archived.evidence_refs == second.evidence_refs
    assert reopened.request_maintenance(maintenance('merge', canonical, archived, key='repeat')).status == 'no_change'
    assert reopened.request_maintenance(maintenance('restore', archived)).reason_code == 'merged_item'
    assert not c.rollback_calls


def test_merge_preserves_original_write_when_the_chosen_item_was_unchanged(lane):
    store, c, _, _ = lane
    service, (writer, unchanged) = start(lane, count=2)
    original = tuple(store.read_evidence(SCOPE, item.evidence_refs[0]) for item in (writer, unchanged))
    outcome = service.request_maintenance(maintenance('merge', unchanged, writer))
    assert outcome.status == 'recorded'
    (kept,) = service.select_context(SCOPE, TASK)
    assert kept.item_id == unchanged.item_id and kept.evidence_refs == (*unchanged.evidence_refs, *writer.evidence_refs)
    retired = service.request_maintenance(maintenance('disable', kept))
    assert retired.status == 'needs_rollback' and retired.job == writer.job
    assert not service.select_context(SCOPE, TASK)
    assert service.read_history(SCOPE, writer.item_id)[-2] == writer
    assert service.read_history(SCOPE, unchanged.item_id)[-3] == unchanged
    assert tuple(store.read_evidence(SCOPE, item.evidence_refs[0]) for item in (writer, unchanged)) == original
    c.grants.add('approved')
    operation = service.request_rollback(SCOPE, retired.decision_id, 'approved')
    c.finish_rollback(operation)
    assert service.reconcile_learning_item(SCOPE, kept.item_id, operation).state == 'disabled'
    assert c.files[PATH] == BEFORE


def test_stale_merge_retains_both_original_revisions(lane):
    _, c, _, _ = lane
    service, (first, second) = start(lane, count=2)
    c.external_change('# External change\n')
    result = service.request_maintenance(maintenance('merge', first, second))
    assert result.reason_code == 'version_conflict'
    assert service.read_history(SCOPE, first.item_id)[-1] == first
    assert service.read_history(SCOPE, second.item_id)[-1] == second
    assert not service.select_context(SCOPE, TASK)


def test_rollback_requires_c_authorization_and_restore_uses_fresh_candidate(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    decision = service.request_maintenance(maintenance('disable', active))
    with pytest.raises(PortRejected, match='authorization_required'):
        service.request_rollback(SCOPE, decision.decision_id, 'revoked')
    assert RULE in c.files[PATH] and not service.select_context(SCOPE, TASK)
    c.grants.add('approved')
    operation = service.request_rollback(SCOPE, decision.decision_id, 'approved')
    assert service.request_rollback(SCOPE, decision.decision_id, 'approved') == operation
    with pytest.raises(PortRejected, match='idempotency_conflict'):
        service.request_rollback(SCOPE, decision.decision_id, 'different-grant')
    assert len(c.rollback_calls) == 2
    (disabled,) = service.list_learning_items(SCOPE).items
    assert service.request_maintenance(maintenance('restore', disabled, key='queued')).reason_code == 'rollback_pending'
    c.finish_rollback(operation)
    undone = service.reconcile_learning_item(SCOPE, active.item_id, operation)
    assert undone.state == 'disabled' and c.files[PATH] == BEFORE
    assert service.reconcile_learning_item(SCOPE, active.item_id, operation) == undone
    assert service.select_context(SCOPE, TASK) == ()
    rollback_id = decision.decision_id
    decision = service.request_maintenance(maintenance('restore', undone))
    assert decision.status == 'needs_candidate' and decision.candidate is not None
    assert decision.candidate.snapshot_ref != c.jobs[next(iter(c.jobs))][0].snapshot_ref
    submitted = service.submit_candidate(SCOPE, decision.decision_id)
    recovered = service.reconcile_learning_item(SCOPE, active.item_id, c.apply(submitted.job))
    assert recovered.state == 'active' and service.select_context(SCOPE, TASK) == (recovered,)
    assert service.reconcile_learning_item(SCOPE, active.item_id, operation) == recovered
    assert service.request_rollback(SCOPE, rollback_id, 'approved') == operation
    assert len(c.rollback_calls) == 2
    assert [item.state for item in service.read_history(SCOPE, active.item_id)] == [
        'proposed', 'active', 'disabled', 'disabled', 'active',
    ]


def test_pending_restore_receipt_cannot_reactivate_a_newly_archived_item(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    disabled = service.request_maintenance(maintenance('disable', active))
    c.grants.add('approved')
    rollback = service.request_rollback(SCOPE, disabled.decision_id, 'approved')
    c.finish_rollback(rollback)
    undone = service.reconcile_learning_item(SCOPE, active.item_id, rollback)
    restore = service.request_maintenance(maintenance('restore', undone))
    job = service.submit_candidate(SCOPE, restore.decision_id).job

    assert service.request_maintenance(maintenance('archive', undone)).status == 'recorded'
    history = service.read_history(SCOPE, active.item_id)
    late_apply = c.apply(job)
    with pytest.raises(PortRejected, match='stale_operation'):
        service.reconcile_learning_item(SCOPE, active.item_id, late_apply)
    assert service.read_history(SCOPE, active.item_id) == history
    assert history[-1].state == 'archived' and history[-1].evidence_refs == active.evidence_refs
    assert not service.select_context(SCOPE, TASK)


def test_old_rollback_receipt_cannot_confirm_a_newer_version_of_the_same_bytes(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    disabled = service.request_maintenance(maintenance('disable', active))
    c.grants.add('approved')
    operation = service.request_rollback(SCOPE, disabled.decision_id, 'approved')
    c.finish_rollback(operation)
    history = service.read_history(SCOPE, active.item_id)
    c.external_change('# Temporary external edit\n')
    c.external_change(BEFORE)

    with pytest.raises(PortRejected, match='version_conflict'):
        service.reconcile_learning_item(SCOPE, active.item_id, operation)
    assert service.read_history(SCOPE, active.item_id) == history
    assert service.request_maintenance(maintenance('restore', history[-1])).reason_code == 'rollback_pending'
    assert not service.select_context(SCOPE, TASK)


def test_accepted_rollback_with_lost_ack_blocks_restore_and_replays_same_request(lane):
    store, c, _, _ = lane
    service, (active,) = start(lane)
    decision = service.request_maintenance(maintenance('disable', active))
    c.grants.add('approved')
    c.drop_rollback_ack = True
    with pytest.raises(ConnectionError):
        service.request_rollback(SCOPE, decision.decision_id, 'approved')
    operation = c.rollbacks[decision.decision_id][1]
    c.finish_rollback(operation)
    reopened = MaintenanceService(LearningStore(store.path), c, c, c)
    (disabled,) = reopened.list_learning_items(SCOPE).items
    assert reopened.request_maintenance(maintenance('restore', disabled)).reason_code == 'rollback_pending'
    with pytest.raises(PortRejected, match='idempotency_conflict'):
        reopened.request_rollback(SCOPE, decision.decision_id, 'different')
    assert reopened.request_rollback(SCOPE, decision.decision_id, 'approved') == operation
    assert c.rollback_calls[0] == c.rollback_calls[1] and len(c.rollbacks) == 1
    assert reopened.reconcile_learning_item(SCOPE, active.item_id, operation).state == 'disabled'


def test_c_rechecks_revoked_authorization_before_a_queued_rollback(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    decision = service.request_maintenance(maintenance('disable', active))
    original = c.files[PATH]
    c.grants.add('approved')
    operation = service.request_rollback(SCOPE, decision.decision_id, 'approved')
    c.grants.remove('approved')
    c.finish_rollback(operation)
    with pytest.raises(PortRejected, match='authorization_required'):
        service.reconcile_learning_item(SCOPE, active.item_id, operation)
    latest = service.read_history(SCOPE, active.item_id)[-1]
    assert latest.state == 'disabled' and latest.version_receipt_ref == active.version_receipt_ref
    assert c.files[PATH] == original and not service.select_context(SCOPE, TASK)


def test_version_drift_and_c_rollback_conflict_preserve_files_and_history(lane):
    _, c, _, _ = lane
    service, (active,) = start(lane)
    decision = service.request_maintenance(maintenance('disable', active))
    c.grants.add('approved')
    c.force_conflict = True
    with pytest.raises(PortRejected, match='rollback_conflict'):
        service.request_rollback(SCOPE, decision.decision_id, 'approved')
    history = service.read_history(SCOPE, active.item_id)
    assert history[-1].version_receipt_ref == active.version_receipt_ref
    c.external_change('# Independent edit\n' + RULE)
    with pytest.raises(PortRejected, match='version_conflict'):
        service.request_rollback(SCOPE, decision.decision_id, 'approved')
    assert len(c.rollback_calls) == 1
    assert service.request_maintenance(maintenance('restore', history[-1])).reason_code == 'version_conflict'
    assert service.read_history(SCOPE, active.item_id) == history
    assert c.files[PATH].startswith('# Independent edit')


def test_wrong_scope_and_receipt_cannot_activate_items(lane):
    store, c, reflection, (first, _) = lane
    service = MaintenanceService(store, c, c, c)
    reflection.submit(first)
    item = service.track_reflection(first)
    op = c.apply(item.job)
    with pytest.raises(PortRejected, match='out_of_scope'):
        service.reconcile_learning_item(OTHER, item.item_id, op)
    ref = c.operations[op].version_receipt_ref
    receipt = c.receipts[(op.job, ref)]
    c.receipts[(op.job, ref)] = replace(receipt, candidate_sha256='f' * 64)
    with pytest.raises(PortRejected, match='candidate_mismatch'):
        service.reconcile_learning_item(SCOPE, item.item_id, op)
    assert service.list_learning_items(SCOPE).items == (item,)
    assert not service.select_context(SCOPE, TASK)
    page = service.list_learning_items(SCOPE)
    with pytest.raises(PortRejected, match='invalid_cursor'):
        service.list_learning_items(OTHER, after=page.next_after)


def test_old_apply_receipt_cannot_adopt_matching_bytes_in_a_new_version(lane):
    store, c, reflection, (first, _) = lane
    service = MaintenanceService(store, c, c, c)
    reflection.submit(first)
    item = service.track_reflection(first)
    operation = c.apply(item.job)
    body = c.files[PATH]
    c.external_change('# Temporary external edit\n')
    c.external_change(body)
    with pytest.raises(PortRejected, match='version_conflict'):
        service.reconcile_learning_item(SCOPE, item.item_id, operation)
    assert service.read_history(SCOPE, item.item_id) == (item,)
    assert not service.select_context(SCOPE, TASK)


def test_existing_revision_cannot_select_if_its_old_binding_lacks_version_proof(lane):
    store, c, _, _ = lane
    service, (active,) = start(lane)
    with closing(store.connection()) as db, db:
        raw = db.execute('SELECT body FROM learning_revisions WHERE item_id=? AND revision=?',
                         (active.item_id, active.revision)).fetchone()['body']
        old = decode(raw)
        old.pop('versions_verified', None)
        db.execute('UPDATE learning_revisions SET body=? WHERE item_id=? AND revision=?',
                   (encode(old), active.item_id, active.revision))
    assert service.select_context(SCOPE, TASK) == (active,)

    # Older records accepted matching bytes and could pin the newer, wrong version ID.
    body = c.files[PATH]
    c.external_change('# Temporary external edit\n')
    c.external_change(body)
    with closing(store.connection()) as db, db:
        old['versions'][0]['version_id'] = c.read_current_versions(SCOPE, (TARGET,))[0].version_id
        db.execute('UPDATE learning_revisions SET body=? WHERE item_id=? AND revision=?',
                   (encode(old), active.item_id, active.revision))
    reopened = MaintenanceService(LearningStore(store.path), c, c, c)
    assert not reopened.select_context(SCOPE, TASK)
    assert reopened.read_history(SCOPE, active.item_id)[-1] == active


def test_upgrading_v2_preserves_saved_d2_candidate(lane):
    store, c, reflection, (first, _) = lane
    receipt = reflection.submit(first)
    with closing(store.connection()) as db, db:
        db.execute('DROP TABLE learning_operations')
        db.execute('DROP TABLE maintenance_decisions')
        db.execute('DROP TABLE learning_revisions')
        raw = db.execute('SELECT draft FROM reflections').fetchone()['draft']
        legacy = decode(raw)
        legacy['experience'].pop('text')
        db.execute('UPDATE reflections SET draft=?', (encode(legacy),))
        db.execute('ALTER TABLE reflections DROP COLUMN call_started')
        db.execute('PRAGMA user_version=2')
    service = MaintenanceService(LearningStore(store.path), c, c, c)
    with pytest.raises(PortRejected, match='experience_anchor_required'):
        service.track_reflection(first)
    with pytest.raises(PortRejected, match='invalid_experience_anchor'):
        service.track_reflection(first, legacy_text='# Project rules')
    tracked = service.track_reflection(first, legacy_text=RULE)
    assert tracked.job == receipt.job and tracked.evidence_refs == first.evidence_refs
