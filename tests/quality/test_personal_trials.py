"""D5 offline second-host compatibility and scoped, multi-round acceptance."""
from contextlib import closing
from dataclasses import replace

import pytest

from harnessgrad.improvement.contracts import text_diff
from harnessgrad.personal.contracts import decode, digest, encode, source_events
from harnessgrad.personal.evidence import EvidenceSelection
from harnessgrad.personal.fixtures import example
from harnessgrad.personal.learning.contracts import LearningPolicyUpdate, LearningScope, SourceItem
from harnessgrad.personal.learning.extension_contracts import ComparisonReport, RunRecord
from harnessgrad.personal.learning.maintenance import MaintenanceService
from harnessgrad.personal.learning.maintenance_contracts import ItemRevision, MaintenanceRequest, TaskContext
from harnessgrad.personal.learning.reflection import ReflectionService
from harnessgrad.personal.learning.reflection_contracts import PortRejected, ReflectionInput
from harnessgrad.personal.learning.service import LearningService
from harnessgrad.personal.learning.source import SelectedImportSource
from harnessgrad.personal.learning.store import LearningStore
from harnessgrad.personal.learning.trial_contracts import (
    CAPABILITIES, CapabilityCheck, CapabilityView, HostProbe, LoadObservation, SelectedHost, TrialJob, TrialRecord,
)
from harnessgrad.personal.learning.trials import TrialService
from personal_maintenance_fixtures import BEFORE, PATH, SCOPE, TARGET, FixtureC

OTHER = LearningScope(SCOPE.profile_id, 'other-project')
HOST = 'opencode'
VERSION = 'fixture-1'
CONTROLLED = 'opencode-controlled'
BYPASS = 'opencode-bypass'
SCENARIO = 'python-edit'
CONTEXT = TaskContext((TARGET,), (SCENARIO,))
RULE = '- Run the selected tests after editing Python.'
CORRECTION = '- Run the selected tests after editing Python and report the results.'
OTHER_RULE = '- Confirm schema changes with the second project owner.'
OTHER_CORRECTION = '- Confirm schema changes and record the second project owner’s approval.'


class FixtureTrialPort:
    """Scoped observations from explicit fixture hosts and the shared fake version service."""

    def __init__(self, ports):
        self.ports = ports
        self.bindings = {}
        self.jobs = {}
        self.observations = {}
        self.comparisons = {}
        self.runs = {}
        for scope in ports:
            binding = SelectedHost(scope, CONTROLLED, HOST, VERSION, 'controlled', 'windows')
            self._bind(binding, {'selected_read', 'render', 'retest', 'apply', 'rollback', 'observe_load'})
        self._bind(SelectedHost(SCOPE, BYPASS, HOST, VERSION, 'bypass', 'ubuntu-24.04'),
                   {'selected_read', 'render'})

    def _bind(self, binding, available):
        checks = tuple(CapabilityCheck(name, 'ready' if name in available else 'unavailable',
                                       None if name in available else 'fixture_mode_unavailable')
                       for name in CAPABILITIES)
        self.bindings[(binding.scope, binding.host_binding_ref)] = HostProbe(
            binding, CapabilityView(binding.scope, binding.host_version, binding.mode, checks), True,
        )

    @staticmethod
    def _get(registry, key):
        if key not in registry:
            raise PortRejected('out_of_scope')
        return registry[key]

    def probe_host(self, scope, host_binding_ref):
        return self._get(self.bindings, (scope, host_binding_ref))

    def export_diff(self, scope, binding_ref, proposed):
        probe = self.probe_host(scope, binding_ref)
        if probe.fallback != 'diff_export':
            raise PortRejected('unavailable')
        c = self.ports[scope]
        snapshot = c.freeze_snapshot(scope, (TARGET,))
        before = c.read_artifact(scope, snapshot.snapshot_ref, kind='virtual_snapshot').payload['files']
        return text_diff(before, proposed)

    def bind_job(self, job, host_binding_ref=CONTROLLED):
        scope = LearningScope(job.profile_id, job.project_id)
        self._get(self.ports, scope).check_job(job)
        probe = self.probe_host(scope, host_binding_ref)
        if probe.fallback is not None:
            raise PortRejected('unavailable')
        metadata = TrialJob(job, probe.binding, SCENARIO, fixture=True, real_model_calls=0)
        if self.jobs.setdefault(job, metadata) != metadata:
            raise PortRejected('host_binding_mismatch')

    def read_trial_job(self, job):
        return self._get(self.jobs, job)

    def read_version_receipt(self, job, ref):
        metadata = self.read_trial_job(job)
        return self.ports[metadata.binding.scope].read_version_receipt(job, ref)

    def read_operation(self, operation):
        metadata = self.read_trial_job(operation.job)
        return self.ports[metadata.binding.scope].read_operation(operation)

    def _scripted_run(self, job, phase, trace, expected, snapshot_sha256, *, prepared_sha256=None,
                      operation=None, new_thread_ref=None, loaded_version_ref=None):
        data = self.read_trial_job(job)
        ref = 'run_' + digest([as_key(job), phase, trace, expected, snapshot_sha256, prepared_sha256,
                               operation.operation_id if operation else None, new_thread_ref, loaded_version_ref])
        self.runs[(job, ref)] = RunRecord(
            ref, job, data.scenario_id, phase, 1, digest([data.scenario_id, 'scripted-task-v1']), snapshot_sha256,
            prepared_sha256, None, (), None, None, False, 'executed',
            'met' if trace == expected else 'not_met', None, True, operation=operation,
            new_thread_ref=new_thread_ref, loaded_version_ref=loaded_version_ref,
        )
        return ref

    def compare(self, job, baseline, candidate, expected):
        scope = self.read_trial_job(job).binding.scope
        prepared = self.ports[scope].read_job_candidate(job)
        same_input = digest([as_key(job), SCENARIO])
        base = self._scripted_run(job, 'baseline', baseline, expected, same_input)
        revised = self._scripted_run(job, 'candidate', candidate, expected, same_input,
                                     prepared_sha256=prepared.candidate_sha256)
        ref = 'report_' + digest([base, revised])
        verdict = 'retest_passed' if candidate == expected else 'retest_failed'
        self.comparisons[(job, ref)] = ComparisonReport(
            ref, job, prepared.candidate_ref, (base,), (revised,), True, verdict, None, True,
            candidate_sha256=prepared.candidate_sha256,
        )
        return ref

    def observe(self, operation, trace=None, expected=None):
        job = operation.job
        metadata = self.read_trial_job(job)
        view = self.read_operation(operation)
        receipt = self.read_version_receipt(job, view.version_receipt_ref)
        c = self.ports[metadata.binding.scope]
        assert c.version == receipt.after_version_ref  # The synthetic new thread opens this exact version.
        ref = 'observation_' + digest([as_key(job), receipt.receipt_ref])
        thread = 'thread_' + digest(ref)
        behavior_ref = (self._scripted_run(job, 'post_load_behavior', trace, expected,
                                           digest([ref, 'new-thread-input']), operation=operation,
                                           new_thread_ref=thread, loaded_version_ref=c.version)
                        if trace is not None else None)
        self.observations[(job, ref)] = LoadObservation(
            ref, job, receipt.receipt_ref, VERSION, metadata.binding.mode, thread,
            receipt.after_version_ref, 'loaded',
            self.runs[(job, behavior_ref)].judge_result if behavior_ref else 'unchecked',
            'load-evidence_' + digest([ref, c.files]), behavior_ref, True,
        )
        return ref

    def read_load_observation(self, job, ref):
        return self._get(self.observations, (job, ref))

    def read_comparison(self, job, ref):
        return self._get(self.comparisons, (job, ref))

    def read_run(self, job, ref):
        return self._get(self.runs, (job, ref))


def as_key(job):
    return job.profile_id, job.project_id, job.job_id


def adopt(store, c, scope, rule):
    fixture = example()
    data = {**fixture['events'][2], 'event_id': 'same-event', 'text': rule}
    (event,) = source_events({**fixture, 'events': [data]})
    selected = SourceItem(event, EvidenceSelection(event.source_instance, event.session_id, event.event_id,
                                                   digest(data), 0, len(event.text)), 'reusable', task_boundary=True)
    grant = LearningPolicyUpdate(scope, 0, True, (event.source_instance,), 0, 8, 'enable', 'local-grant')
    intake = LearningService(store, SelectedImportSource(scope, event.source_instance, (selected,)),
                             confirm_policy=lambda request: request == grant)
    intake.update_learning_policy(grant)
    evidence = intake.poll_source(scope, event.source_instance).receipts[0].decision.evidence_refs
    reflection = ReflectionService(
        store, c, c, read_existing=lambda scope, snapshot: (),
        ask=lambda _: {'decision': 'propose', 'kind': 'rule', 'topic_id': 'project-rule', 'path': PATH, 'text': rule,
                       'conditions': [SCENARIO], 'criterion': 'Observe the scripted delivery behavior.'},
    )
    request = ReflectionInput(scope, evidence, c.freeze_snapshot(scope, (TARGET,)), SCENARIO)
    job = reflection.submit(request).job
    service = MaintenanceService(store, c, c, c)
    item = service.track_reflection(request)
    op = c.apply(job)
    active = service.reconcile_learning_item(scope, item.item_id, op)
    assert service.select_context(scope, CONTEXT) == (active,)
    return service, active, op


def record(job, *, round=1, **refs):
    scope = LearningScope(job.profile_id, job.project_id)
    return TrialRecord(scope, CONTROLLED, VERSION, 'controlled', 'windows', SCENARIO, round, job,
                       True, f'round-{round}', **refs)


def maintenance(scope, item, action, text=None):
    return MaintenanceRequest(scope, action, (ItemRevision(item.item_id, item.revision),),
                              f'fixture {action}', action, text)


def test_second_host_matrix_includes_supported_and_read_only_modes(tmp_path):
    c = FixtureC()
    port = FixtureTrialPort({SCOPE: c})
    service = TrialService(LearningStore(tmp_path / 'learning.sqlite3'), port)
    controlled, bypass = service.compatibility_matrix(((SCOPE, CONTROLLED), (SCOPE, BYPASS)))
    assert [(row.binding.host_id, row.view.host_version, row.view.mode, row.binding.os, row.fixture)
            for row in (controlled, bypass)] == [
                (HOST, VERSION, 'controlled', 'windows', True),
                (HOST, VERSION, 'bypass', 'ubuntu-24.04', True),
    ]
    assert controlled.fallback is None and bypass.fallback == 'diff_export'
    states = {check.capability: check.status for check in bypass.view.checks}
    assert states['selected_read'] == states['render'] == 'ready'
    assert states['apply'] == states['rollback'] == states['observe_load'] == 'unavailable'
    assert '+- Run the selected tests' in port.export_diff(SCOPE, BYPASS, {PATH: BEFORE + RULE + '\r\n'})
    assert c.files == {PATH: BEFORE} and c.version == 'version-0' and not c.operations
    with pytest.raises(PortRejected, match='out_of_scope'):
        service.probe_host(OTHER, CONTROLLED)
    with pytest.raises(PortRejected, match='out_of_scope'):
        port.export_diff(OTHER, BYPASS, {PATH: RULE})
    _, item, _ = adopt(service.store, c, SCOPE, RULE)
    with pytest.raises(PortRejected, match='unavailable'):
        port.bind_job(item.job, BYPASS)


def test_probe_does_not_guess_version_or_unreported_capabilities(tmp_path):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    port = FixtureTrialPort({SCOPE: FixtureC()})
    service = TrialService(store, port)
    original = port.bindings[SCOPE, CONTROLLED]
    reported = (CapabilityCheck('selected_read', 'ready'),)
    port.bindings[SCOPE, CONTROLLED] = replace(original, view=replace(original.view, host_version=None, checks=reported))
    row, = service.compatibility_matrix(((SCOPE, CONTROLLED),))
    assert row.view.host_version is None and row.fallback == 'manual_import'
    assert all(check.status == 'unchecked' for check in row.view.checks)
    duplicate = replace(original.view, checks=reported + reported)
    port.bindings[SCOPE, CONTROLLED] = replace(original, view=duplicate)
    with pytest.raises(PortRejected, match='invalid_capability_view'):
        service.probe_host(SCOPE, CONTROLLED)


@pytest.mark.parametrize(('capability', 'status', 'fallback'), (
    ('selected_read', 'unavailable', 'manual_import'),
    ('render', 'refused', 'manual_import'),
    ('retest', None, 'diff_export'),
    ('apply', 'refused', 'diff_export'),
    ('rollback', 'unavailable', None),
    ('observe_load', 'unavailable', None),
))
def test_capability_fallback_is_scoped_to_the_missing_step(tmp_path, capability, status, fallback):
    port = FixtureTrialPort({SCOPE: FixtureC()})
    service = TrialService(LearningStore(tmp_path / 'learning.sqlite3'), port)
    probe = port.bindings[SCOPE, CONTROLLED]
    checks = tuple(check for check in probe.view.checks if check.capability != capability)
    if status is not None:
        checks += (CapabilityCheck(capability, status, 'fixture_missing_capability'),)
    port.bindings[SCOPE, CONTROLLED] = replace(probe, view=replace(probe.view, checks=checks))
    row, = service.compatibility_matrix(((SCOPE, CONTROLLED),))
    assert row.fallback == fallback
    assert next(check.status for check in row.view.checks if check.capability == capability) == (status or 'unchecked')
    if capability == 'retest':
        assert '+- Run the selected tests' in port.export_diff(SCOPE, CONTROLLED, {PATH: BEFORE + RULE + '\r\n'})


def test_matrix_keeps_fixture_results_separate_from_an_unchecked_binding(tmp_path):
    port = FixtureTrialPort({SCOPE: FixtureC(), OTHER: FixtureC(OTHER)})
    service = TrialService(LearningStore(tmp_path / 'learning.sqlite3'), port)
    other = port.bindings[OTHER, CONTROLLED]
    port.bindings[OTHER, CONTROLLED] = replace(other, fixture=False, view=replace(other.view, checks=()))
    bindings = ((SCOPE, CONTROLLED), (OTHER, CONTROLLED))
    fixture, unchecked = service.compatibility_matrix(bindings)
    assert fixture.fixture is True and fixture.fallback is None
    assert unchecked.fixture is False and unchecked.fallback == 'manual_import'
    assert all(check.status == 'unchecked' for check in unchecked.view.checks)
    assert service.compatibility_matrix(bindings[::-1]) == (unchecked, fixture)


def test_multi_round_trials_and_project_isolation(tmp_path, monkeypatch):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    a, b = FixtureC(SCOPE), FixtureC(OTHER)
    port = FixtureTrialPort({SCOPE: a, OTHER: b})
    trials = TrialService(store, port)
    first, adopted, initial = adopt(store, a, SCOPE, RULE)
    second, unrelated, other_apply = adopt(store, b, OTHER, OTHER_RULE)
    assert adopted.job.job_id == unrelated.job.job_id
    assert adopted.item_id != unrelated.item_id and adopted.evidence_refs != unrelated.evidence_refs
    assert a.version == b.version

    port.bind_job(adopted.job)
    port.bind_job(unrelated.job)
    initial_ref = port.read_operation(initial).version_receipt_ref
    other_ref = port.read_operation(other_apply).version_receipt_ref
    assert initial_ref == other_ref  # Equal-looking receipt IDs in distinct C job scopes.
    expected = ('run-tests', 'final-reply')
    comparison = port.compare(adopted.job, ('final-reply',), expected, expected)
    observation = port.observe(initial, trace=expected, expected=expected)
    a_round1 = record(adopted.job, comparison_ref=comparison, version_receipt_ref=initial_ref,
                      observation_ref=observation)
    b_round1 = record(unrelated.job, version_receipt_ref=other_ref)
    first_ref = trials.record_trial(a_round1)
    other_trial = trials.record_trial(b_round1)
    assert first_ref != other_trial
    first_details = trials.read_trial(SCOPE, first_ref.trial_ref)
    assert first_details.comparison_verdict == 'retest_passed' and first_details.version_status == 'applied'
    assert (first_details.load_state, first_details.behavior_state, first_details.real_model_calls) == ('loaded', 'met', 0)
    assert trials.read_trial(OTHER, other_trial.trial_ref).load_state == 'unchecked'
    with pytest.raises(PortRejected, match='out_of_scope'):
        trials.record_trial(replace(a_round1, scope=OTHER, idempotency_key='foreign-job'))
    with pytest.raises(PortRejected, match='out_of_scope'):
        trials.record_trial(replace(b_round1, observation_ref=observation, idempotency_key='foreign-observation'))
    with pytest.raises(PortRejected, match='out_of_scope'):
        trials.read_trial(OTHER, first_ref.trial_ref)
    with pytest.raises(PortRejected, match='out_of_scope'):
        second.reconcile_learning_item(OTHER, unrelated.item_id, initial)
    with pytest.raises(PermissionError):
        store.read_evidence(OTHER, adopted.evidence_refs[0])

    decision = first.request_maintenance(maintenance(SCOPE, adopted, 'correct', CORRECTION))
    other_decision = second.request_maintenance(maintenance(OTHER, unrelated, 'correct', OTHER_CORRECTION))
    assert decision.decision_id != other_decision.decision_id
    with pytest.raises(PortRejected, match='out_of_scope'):
        second.submit_candidate(OTHER, decision.decision_id)
    corrected_job = first.submit_candidate(SCOPE, decision.decision_id).job
    other_corrected_job = second.submit_candidate(OTHER, other_decision.decision_id).job
    assert corrected_job.job_id == other_corrected_job.job_id
    port.bind_job(corrected_job)
    port.bind_job(other_corrected_job)
    applied = a.apply(corrected_job)
    other_applied = b.apply(other_corrected_job)
    with pytest.raises(PortRejected, match='out_of_scope'):
        second.reconcile_learning_item(OTHER, unrelated.item_id, applied)
    learned = first.reconcile_learning_item(SCOPE, adopted.item_id, applied)
    other_learned = second.reconcile_learning_item(OTHER, unrelated.item_id, other_applied)
    assert learned.summary == CORRECTION and first.select_context(SCOPE, CONTEXT) == (learned,)
    assert other_learned.summary == OTHER_CORRECTION
    assert learned.version_receipt_ref == other_learned.version_receipt_ref
    corrected_expected = ('run-tests', 'report-results', 'final-reply')
    a_round2 = record(
        corrected_job, round=2, version_receipt_ref=learned.version_receipt_ref,
        observation_ref=port.observe(applied, trace=corrected_expected, expected=corrected_expected),
    )
    b_round2 = record(other_corrected_job, round=2, version_receipt_ref=other_learned.version_receipt_ref)
    a_corrected = trials.record_trial(a_round2)
    foreign_receipt = port.read_version_receipt(corrected_job, learned.version_receipt_ref)
    with monkeypatch.context() as patch:
        patch.setattr(port, 'read_version_receipt', lambda *_: foreign_receipt)
        with pytest.raises(PortRejected, match='out_of_scope'):
            trials.record_trial(b_round2)
    b_corrected = trials.record_trial(b_round2)
    assert a_corrected != b_corrected
    assert trials.read_trial(SCOPE, a_corrected.trial_ref).behavior_state == 'met'
    assert trials.read_trial(OTHER, b_corrected.trial_ref).behavior_state == 'unchecked'
    before_other = (dict(b.files), second.read_history(OTHER, unrelated.item_id))

    selected_before_disable = first.select_context(SCOPE, CONTEXT)
    retire = first.request_maintenance(maintenance(SCOPE, learned, 'disable'))
    assert retire.status == 'needs_rollback' and not first.select_context(SCOPE, CONTEXT)
    assert selected_before_disable == (learned,)  # A task that already selected it keeps its context.
    assert second.select_context(OTHER, CONTEXT) == (other_learned,)
    disabled_ref = trials.record_trial(record(corrected_job, round=3))
    disabled_trial = trials.read_trial(SCOPE, disabled_ref.trial_ref)
    assert disabled_trial.version_status is None and disabled_trial.behavior_state == 'unchecked'

    a.grants.add('project-a-authorization')
    with pytest.raises(PortRejected, match='out_of_scope'):
        second.request_rollback(OTHER, retire.decision_id, 'project-a-authorization')
    rollback = first.request_rollback(SCOPE, retire.decision_id, 'project-a-authorization')
    a.finish_rollback(rollback)
    rolled_back = first.reconcile_learning_item(SCOPE, adopted.item_id, rollback)
    a_round4 = record(
        corrected_job, round=4, version_receipt_ref=rolled_back.version_receipt_ref,
        observation_ref=port.observe(rollback),
    )
    final_ref = trials.record_trial(a_round4)
    assert rolled_back.state == 'disabled' and a.files[PATH] == BEFORE + '\r\n' + RULE + '\r\n'
    assert not first.select_context(SCOPE, CONTEXT)
    assert (b.files, second.read_history(OTHER, unrelated.item_id)) == before_other
    assert second.select_context(OTHER, CONTEXT) == (other_learned,)

    b_retire = second.request_maintenance(maintenance(OTHER, other_learned, 'disable'))
    with pytest.raises(PortRejected, match='authorization_required'):
        second.request_rollback(OTHER, b_retire.decision_id, 'project-a-authorization')
    b.grants.add('project-b-authorization')
    b_rollback = second.request_rollback(OTHER, b_retire.decision_id, 'project-b-authorization')
    assert rollback.operation_id == b_rollback.operation_id and rollback.job != b_rollback.job
    b.finish_rollback(b_rollback)
    with pytest.raises(PortRejected, match='out_of_scope'):
        second.reconcile_learning_item(OTHER, unrelated.item_id, rollback)
    b_rolled_back = second.reconcile_learning_item(OTHER, unrelated.item_id, b_rollback)
    b_round4 = record(other_corrected_job, round=4, version_receipt_ref=b_rolled_back.version_receipt_ref)
    assert b_rolled_back.version_receipt_ref == rolled_back.version_receipt_ref
    other_final_ref = trials.record_trial(b_round4)
    assert final_ref != other_final_ref
    assert b.files[PATH] == BEFORE + '\r\n' + OTHER_RULE + '\r\n'

    reopened_store = LearningStore(store.path)
    reopened = TrialService(reopened_store, port)
    with monkeypatch.context() as patch:
        patch.setattr(port, 'read_trial_job', lambda _: pytest.fail('replay must use the saved trial'))
        for request, ref in ((a_round1, first_ref), (b_round1, other_trial), (a_round2, a_corrected),
                             (b_round2, b_corrected), (a_round4, final_ref), (b_round4, other_final_ref)):
            assert reopened.record_trial(request) == ref
    assert reopened.read_trial(SCOPE, first_ref.trial_ref) == first_details
    assert reopened.read_trial(SCOPE, final_ref.trial_ref).version_status == 'rolled_back'
    assert reopened.read_trial(SCOPE, final_ref.trial_ref).behavior_state == 'unchecked'
    assert reopened_store.read_evidence(SCOPE, adopted.evidence_refs[0])['text'] == RULE
    with pytest.raises(PortRejected, match='idempotency_conflict'):
        reopened.record_trial(replace(a_round1, round=5))


def test_completed_trial_keeps_its_binding_when_current_host_changes(tmp_path):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    _, active, operation = adopt(store, c, SCOPE, RULE)
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    observation = port.observe(operation, trace=('test',), expected=('test',))
    completed = record(active.job, version_receipt_ref=active.version_receipt_ref, observation_ref=observation)
    probe = port.bindings.pop((SCOPE, CONTROLLED))
    trials = TrialService(store, port)
    with pytest.raises(PortRejected, match='fixture_mismatch'):
        trials.record_trial(replace(completed, fixture=False))
    saved = trials.record_trial(completed)
    details = trials.read_trial(SCOPE, saved.trial_ref)
    assert details.record.fixture is True and details.record.host_version == VERSION
    assert (details.version_status, details.load_state, details.behavior_state) == ('applied', 'loaded', 'met')

    upgraded = 'fixture-2'
    port.bindings[SCOPE, CONTROLLED] = replace(probe, binding=replace(probe.binding, host_version=upgraded),
                                              view=replace(probe.view, host_version=upgraded))
    assert trials.probe_host(SCOPE, CONTROLLED).host_version == upgraded
    with pytest.raises(PortRejected, match='host_binding_mismatch'):
        trials.record_trial(replace(completed, host_version=upgraded, idempotency_key='wrong-version'))
    assert trials.record_trial(completed) == saved


def test_trial_requires_matching_host_fixture_and_observation_evidence(tmp_path):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    _, active, operation = adopt(store, c, SCOPE, RULE)
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    trials = TrialService(store, port)
    observation = port.observe(operation, trace=('test',), expected=('test',))
    original = record(active.job, version_receipt_ref=active.version_receipt_ref, observation_ref=observation)
    for bad in (replace(original, host_binding_ref=BYPASS), replace(original, os='ubuntu-24.04')):
        with pytest.raises(PortRejected, match='host_binding_mismatch'):
            trials.record_trial(bad)
    with pytest.raises(PortRejected, match='fixture_mismatch'):
        trials.record_trial(replace(original, fixture=False))
    receipt_key = (active.job, original.version_receipt_ref)
    receipt = c.receipts[receipt_key]
    entry, = receipt.entries
    for entries in ((entry, entry), (replace(entry, status='unchanged'),)):
        c.receipts[receipt_key] = replace(receipt, entries=entries)
        with pytest.raises(PortRejected, match='invalid_version_receipt'):
            trials.record_trial(original)
    c.receipts[receipt_key] = receipt
    key = (active.job, observation)
    actual = port.observations[key]
    port.observations[key] = replace(actual, loaded_version_ref='another-version')
    with pytest.raises(PortRejected, match='invalid_load_observation'):
        trials.record_trial(original)
    port.observations[key] = replace(actual, behavior_run_ref=None)
    with pytest.raises(PortRejected, match='invalid_behavior_observation'):
        trials.record_trial(original)
    port.observations[key] = actual
    report_ref = port.compare(active.job, (), ('test',), ('test',))
    report_key = (active.job, report_ref)
    report = port.comparisons[report_key]
    port.comparisons[report_key] = replace(report, candidate_run_refs=report.candidate_run_refs * 2)
    with pytest.raises(PortRejected, match='invalid_comparison_report'):
        trials.record_trial(replace(original, comparison_ref=report_ref))
    port.comparisons[report_key] = report
    run_key = (active.job, report.candidate_run_refs[0])
    run = port.runs[run_key]
    port.runs[run_key] = replace(run, status='timeout', reason_code='execution_timeout')
    with pytest.raises(PortRejected, match='invalid_comparison_report'):
        trials.record_trial(replace(original, comparison_ref=report_ref))
    port.runs[run_key] = replace(run, prepared_sha256=digest('different-candidate'))
    with pytest.raises(PortRejected, match='invalid_comparison_report'):
        trials.record_trial(replace(original, comparison_ref=report_ref))
    port.runs[run_key] = run
    accepted = trials.record_trial(original)
    assert trials.read_trial(SCOPE, accepted.trial_ref).behavior_state == 'met'


@pytest.mark.parametrize('source', ['baseline', 'candidate', 'not_loaded'])
def test_trial_rejects_behavior_evidence_from_before_loading(tmp_path, source):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    _, active, operation = adopt(store, c, SCOPE, RULE)
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    expected = ('report-results',)
    comparison_ref = port.compare(active.job, expected, expected, expected)
    report = port.read_comparison(active.job, comparison_ref)
    observation_ref = port.observe(operation, trace=(), expected=expected)
    key = active.job, observation_ref
    observation = port.observations[key]
    request = record(active.job, comparison_ref=comparison_ref, version_receipt_ref=active.version_receipt_ref,
                     observation_ref=observation_ref)
    trials = TrialService(store, port)

    ref = trials.record_trial(request)
    assert trials.read_trial(SCOPE, ref.trial_ref).behavior_state == 'not_met'

    (old_run,) = report.candidate_run_refs if source == 'candidate' else report.baseline_run_refs
    substitute = replace(observation, behavior_state='met', behavior_run_ref=old_run)
    if source == 'not_loaded':
        substitute = replace(substitute, load_state='not_loaded', new_thread_ref=None,
                             loaded_version_ref=None, evidence_ref=None)
    port.observations[key] = substitute
    with pytest.raises(PortRejected, match='invalid_behavior_observation'):
        trials.record_trial(replace(request, idempotency_key='old-run'))


@pytest.mark.parametrize('field', ['operation', 'new_thread_ref', 'loaded_version_ref'])
def test_post_load_run_must_match_the_observed_operation_thread_and_version(tmp_path, field):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    _, active, operation = adopt(store, c, SCOPE, RULE)
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    observation_ref = port.observe(operation, trace=('report-results',), expected=('report-results',))
    observation = port.read_load_observation(active.job, observation_ref)
    run_key = active.job, observation.behavior_run_ref
    run = port.runs[run_key]
    request = record(active.job, version_receipt_ref=active.version_receipt_ref, observation_ref=observation_ref)
    trials = TrialService(store, port)

    previous = replace(operation, operation_id='other-operation') if field == 'operation' else 'old-' + field
    port.runs[run_key] = replace(run, **{field: previous})
    with pytest.raises(PortRejected, match='invalid_behavior_observation'):
        trials.record_trial(request)

    port.runs[run_key] = run
    ref = trials.record_trial(request)
    assert trials.read_trial(SCOPE, ref.trial_ref).behavior_state == 'met'


@pytest.mark.parametrize('changes', [
    {'candidate_id': 'another-candidate'},
    {'candidate_sha256': 'a' * 64},
    {'candidate_id': None, 'candidate_sha256': None},
])
def test_trial_requires_retest_and_apply_to_name_the_same_candidate(tmp_path, changes):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    _, active, operation = adopt(store, c, SCOPE, RULE)
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    comparison_ref = port.compare(active.job, (), ('run-tests',), ('run-tests',))
    version_ref = c.read_operation(operation).version_receipt_ref
    key = active.job, version_ref
    receipt = c.receipts[key]
    request = record(active.job, comparison_ref=comparison_ref, version_receipt_ref=version_ref)
    trials = TrialService(store, port)

    ref = trials.record_trial(request)
    result = trials.read_trial(SCOPE, ref.trial_ref)
    assert (result.comparison_verdict, result.version_status) == ('retest_passed', 'applied')

    c.receipts[key] = replace(receipt, **changes)
    with pytest.raises(PortRejected, match='candidate_mismatch'):
        trials.record_trial(replace(request, idempotency_key='wrong-candidate'))


def test_legacy_trial_keeps_its_receipt_without_reporting_unbound_results(tmp_path, monkeypatch):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    _, active, operation = adopt(store, c, SCOPE, RULE)
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    expected = ('report-results',)
    request = record(active.job, comparison_ref=port.compare(active.job, (), expected, expected),
                     version_receipt_ref=active.version_receipt_ref,
                     observation_ref=port.observe(operation, trace=expected, expected=expected))
    trials = TrialService(store, port)
    ref = trials.record_trial(request)
    select = 'SELECT body FROM learning_trials WHERE id=?'
    with store.transaction() as db:
        body = decode(db.execute(select, (ref.trial_ref,)).fetchone()['body'])
        del body['evidence_version']
        original = encode(body)
        db.execute('UPDATE learning_trials SET body=? WHERE id=?', (original, ref.trial_ref))

    monkeypatch.setattr(port, 'read_trial_job', lambda _: pytest.fail('legacy read must not consult the port'))
    reopened = TrialService(LearningStore(store.path), port)
    assert reopened.record_trial(request) == ref
    receipt = reopened.read_trial(SCOPE, ref.trial_ref)
    assert receipt.record == request
    assert (receipt.version_status, receipt.load_state) == ('applied', 'loaded')
    assert (receipt.comparison_verdict, receipt.behavior_state) == (None, 'unchecked')
    with closing(store.connection()) as db:
        assert db.execute(select, (ref.trial_ref,)).fetchone()['body'] == original


def test_failed_retest_also_requires_the_executed_candidate_digest(tmp_path):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    _, active, _ = adopt(store, c, SCOPE, RULE)
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    comparison_ref = port.compare(active.job, (), ('run-tests',), ('report-results',))
    report = port.read_comparison(active.job, comparison_ref)
    request = record(active.job, comparison_ref=comparison_ref, version_receipt_ref=active.version_receipt_ref)
    trials = TrialService(store, port)
    ref = trials.record_trial(request)
    assert trials.read_trial(SCOPE, ref.trial_ref).comparison_verdict == 'retest_failed'

    (run_ref,) = report.candidate_run_refs
    run = port.read_run(active.job, run_ref)
    port.runs[active.job, run_ref] = replace(run, prepared_sha256=digest('different-candidate'))
    with pytest.raises(PortRejected, match='invalid_comparison_report'):
        trials.record_trial(replace(request, idempotency_key='wrong-tested-candidate'))


@pytest.mark.parametrize('version', [3, 6])
def test_upgrade_adds_trials_and_preserves_maintenance_history(tmp_path, version):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    c = FixtureC()
    service, active, operation = adopt(store, c, SCOPE, RULE)
    history = service.read_history(SCOPE, active.item_id)
    with closing(store.connection()) as db, db:
        db.execute('DROP TABLE learning_trials')
        if version < 5:
            db.execute('ALTER TABLE reflections DROP COLUMN call_started')
        db.execute(f'PRAGMA user_version={version}')
    upgraded = LearningStore(store.path)
    assert MaintenanceService(upgraded, c, c, c).read_history(SCOPE, active.item_id) == history
    port = FixtureTrialPort({SCOPE: c})
    port.bind_job(active.job)
    trials = TrialService(upgraded, port)
    ref = trials.record_trial(record(active.job, version_receipt_ref=c.read_operation(operation).version_receipt_ref))
    assert trials.read_trial(SCOPE, ref.trial_ref).version_status == 'applied'
