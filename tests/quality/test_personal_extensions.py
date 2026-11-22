"""D4 offline validation and separately authorized invocation through a fake C port."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Event

import pytest

from harnessgrad.personal.contracts import digest
from harnessgrad.personal.learning.extension_contracts import ExtensionInvocationRequest, ExtensionRequest
from harnessgrad.personal.learning.extensions import EXTENSION_KIND, ExtensionService, ProviderCatalog
from harnessgrad.personal.learning.reflection_contracts import PortRejected
from personal_extension_fixtures import (
    BRIDGE, CAPABILITY, COMPLETE, MISSING, OTHER, PROVIDER_VERSION, SCENARIO_ID, SCOPE,
    FixtureC, Inventory, candidate_spec, provider,
)


@pytest.fixture
def extension():
    c = FixtureC()
    return ExtensionService(c.catalog, c), c


def submit(service, c):
    proposal = service.propose_extension(candidate_spec())
    grant = c.allow_validation(proposal)
    request = ExtensionRequest(SCOPE, proposal.ref, proposal.sha256, grant, 'validate')
    return proposal, request, service.submit_extension(request)


def validated(service, c):
    proposal, request, job = submit(service, c)
    return proposal, request, job, c.finish_validation(job)


def invocation(job, report, item, grant, *, key='invoke'):
    return ExtensionInvocationRequest(job, report.report_ref, item.ref, item.sha256, BRIDGE, grant, key)


@pytest.mark.parametrize('inventory,expected', [
    (COMPLETE, {'ready': True, 'missing_files': []}),
    (MISSING, {'ready': False, 'missing_files': ['docs/usage.md']}),
])
def test_delivery_check_validation_and_invocation(extension, tmp_path, inventory, expected):
    service, c = extension
    project = tmp_path / 'project'
    project.mkdir()
    original = project / 'README.md'
    original.write_bytes(b'Local project evidence stays intact.\n')
    proposal, request, job = submit(service, c)
    assert proposal.kind == EXTENSION_KIND
    assert proposal.sha256 == digest(asdict(candidate_spec()))
    assert service.propose_extension(candidate_spec()) == proposal
    assert service.submit_extension(request) == job
    assert not c.runs  # The queue acknowledgement does not prove that a check ran.

    report = c.finish_validation(job)
    assert c.finish_validation(job) == report
    review = service.read_validation(job, report.report_ref)
    (baseline,), (candidate,) = review.baseline, review.candidate
    assert review.report.fixture and review.report.verdict == 'retest_passed'
    assert baseline.judge_result == 'not_met' and c.output(baseline)['cases']['missing']['ready']
    assert candidate.provider_version == PROVIDER_VERSION and candidate.judge_result == 'met'
    assert c.output(candidate) == {'cases': {
        'complete': {'ready': True, 'missing_files': []},
        'missing': {'ready': False, 'missing_files': ['docs/usage.md']},
    }}
    assert len(c.runs) == 2

    item = c.register_input(inventory)
    grant = c.allow_invocation(job, report.report_ref, item)
    request = invocation(job, report, item, grant)
    operation = service.request_extension_invocation(request)
    queued = service.read_invocation(operation)
    assert queued.operation.status == 'queued' and queued.run is None
    completed = c.finish_invocation(operation)
    receipt = service.read_invocation(operation)
    assert receipt.operation == completed and receipt.operation.status == 'executed'
    assert receipt.run.fixture and receipt.run.phase == 'extension_invoke'
    assert receipt.run.command == () and receipt.run.exit_code is None
    assert receipt.run.provider_version == PROVIDER_VERSION
    assert receipt.run.prepared_sha256 == proposal.sha256
    assert c.output(receipt.run) == expected
    assert receipt.run.judge_result == ('met' if expected['ready'] else 'not_met')
    assert service.request_extension_invocation(request) == operation
    assert c.finish_invocation(operation) == completed and len(c.runs) == 3
    assert original.read_bytes() == b'Local project evidence stays intact.\n'
    assert sorted(path.name for path in project.iterdir()) == ['README.md']


@pytest.mark.parametrize('changes,reason', [
    ({'provider_id': 'unsupported-provider'}, 'untrusted_provider'),
    ({'provider_version': '2.0.0'}, 'untrusted_provider'),
    ({'manifest_sha256': 'a' * 64}, 'untrusted_provider'),
    ({'kind': 'plugin'}, 'untrusted_provider'),
    ({'capability_ids': (CAPABILITY, 'project.write')}, 'capability_not_allowed'),
    ({'scenario_id': 'unknown-scenario'}, 'scenario_unavailable'),
    ({'scope': OTHER}, 'untrusted_provider'),
    ({'evidence_refs': ('unselected-evidence',)}, 'out_of_scope'),
])
def test_proposal_checks_trusted_source_and_permissions(extension, changes, reason):
    service, c = extension
    with pytest.raises(PortRejected, match=reason):
        service.propose_extension(replace(candidate_spec(), **changes))
    assert not c.candidates and not c.jobs and not c.runs


@pytest.mark.parametrize('kind', ['tool', 'plugin'])
def test_trusted_configuration_can_declare_other_extension_kinds(kind):
    definition = replace(provider(), kind=kind)
    c = FixtureC()
    c.catalog = ProviderCatalog((definition,))
    service = ExtensionService(c.catalog, c)
    proposal = service.propose_extension(replace(candidate_spec(), kind=kind))
    assert proposal.kind == EXTENSION_KIND and not c.runs


def test_validation_grant_is_scoped_and_bound_to_the_candidate_digest(extension):
    service, c = extension
    first = service.propose_extension(candidate_spec())
    grant = c.allow_validation(first)
    request = ExtensionRequest(SCOPE, first.ref, first.sha256, grant, 'validate')
    with pytest.raises(PortRejected, match='out_of_scope'):
        service.submit_extension(replace(request, scope=OTHER))
    with pytest.raises(PortRejected, match='candidate_mismatch'):
        service.submit_extension(replace(request, candidate_sha256='b' * 64))

    c.evidence[SCOPE].add('second-selected-evidence')
    second = service.propose_extension(replace(candidate_spec(), evidence_refs=('second-selected-evidence',)))
    with pytest.raises(PortRejected, match='authorization_required'):
        service.submit_extension(replace(request, candidate_ref=second.ref, candidate_sha256=second.sha256))
    c.capabilities.remove('extension_execution')
    with pytest.raises(PortRejected, match='runner_unavailable'):
        service.submit_extension(request)
    assert not c.jobs and not c.runs


def test_provider_allowlist_does_not_authorize_omitting_a_required_capability(extension):
    _, c = extension
    allowed = replace(provider(), capability_ids=(CAPABILITY, 'inventory.summary'))
    c.catalog = ProviderCatalog((allowed,))
    service = ExtensionService(c.catalog, c)
    under_declared = replace(candidate_spec(), capability_ids=('inventory.summary',))
    with pytest.raises(PortRejected, match='capability_required'):
        service.propose_extension(under_declared)
    with pytest.raises(PortRejected, match='capability_required'):
        c.register_extension_candidate(under_declared)
    assert not c.candidates


def test_c_rechecks_frozen_candidate_contents_before_using_a_grant(extension):
    service, c = extension
    original = candidate_spec()
    proposal = service.propose_extension(original)
    grant = c.allow_validation(proposal)
    c.evidence[SCOPE].add('other-approved-evidence')
    substituted = replace(original, evidence_refs=('other-approved-evidence',))
    c.candidates[(SCOPE, proposal.ref)] = proposal, substituted
    with pytest.raises(PortRejected, match='candidate_mismatch'):
        service.submit_extension(ExtensionRequest(SCOPE, proposal.ref, proposal.sha256, grant, 'validate'))
    assert not c.jobs and not c.runs


def test_invocation_needs_a_separate_grant_bound_to_input_report_and_bridge(extension):
    service, c = extension
    _, validation, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    request = invocation(job, report, item, validation.execution_grant_ref)
    with pytest.raises(PortRejected, match='authorization_required'):
        service.request_extension_invocation(request)
    grant = c.allow_invocation(job, report.report_ref, item)
    valid = replace(request, invocation_grant_ref=grant)
    with pytest.raises(PortRejected, match='authorization_required'):
        service.submit_extension(replace(validation, execution_grant_ref=grant, idempotency_key='wrong-lane'))
    different_input = c.register_input(MISSING)
    with pytest.raises(PortRejected, match='authorization_required'):
        service.request_extension_invocation(replace(valid, input_ref=different_input.ref,
                                                     input_sha256=different_input.sha256))
    with pytest.raises(PortRejected, match='input_mismatch'):
        service.request_extension_invocation(replace(valid, input_sha256='a' * 64))
    with pytest.raises(PortRejected, match='untrusted_bridge'):
        service.request_extension_invocation(replace(valid, host_binding_ref='untrusted-bridge'))
    with pytest.raises(PortRejected, match='out_of_scope'):
        service.request_extension_invocation(replace(valid, report_ref='another-report'))
    with pytest.raises(PortRejected, match='out_of_scope'):
        service.request_extension_invocation(replace(valid, job=replace(job, project_id=OTHER.project_id)))
    c.capabilities.remove('extension_invoke')
    with pytest.raises(PortRejected, match='unavailable'):
        service.request_extension_invocation(valid)
    assert not c.invocations and len(c.runs) == 2


@pytest.mark.parametrize('stage', ['validation', 'invocation'])
def test_no_execution_if_grant_is_revoked_while_queued(extension, stage):
    service, c = extension
    proposal, request, job = submit(service, c)
    if stage == 'validation':
        del c.execution_grants[request.execution_grant_ref]
        assert c.allow_validation(proposal) != request.execution_grant_ref
        assert service.submit_extension(request) == job
        report = c.finish_validation(job)
        assert report.verdict == 'not_comparable' and report.reason_code == 'authorization_required'
        assert not c.runs
    else:
        report = c.finish_validation(job)
        item = c.register_input(COMPLETE)
        grant = c.allow_invocation(job, report.report_ref, item)
        call = invocation(job, report, item, grant)
        operation = service.request_extension_invocation(call)
        del c.invocation_grants[grant]
        assert c.allow_invocation(job, report.report_ref, item) != grant
        assert service.request_extension_invocation(call) == operation
        c.finish_invocation(operation)
        receipt = service.read_invocation(operation)
        assert receipt.operation.status == 'failed' and receipt.operation.reason_code == 'authorization_required'
        assert receipt.run is None and len(c.runs) == 2


def test_changed_scenario_or_provider_invalidates_the_proof(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    grant = c.allow_invocation(job, report.report_ref, item)
    call = invocation(job, report, item, grant)
    original = c.scenarios[SCENARIO_ID]
    c.scenarios[SCENARIO_ID] = replace(original, timeout_ms=original.timeout_ms + 10)
    with pytest.raises(PortRejected, match='validation_stale'):
        service.request_extension_invocation(call)
    c.scenarios[SCENARIO_ID] = original
    c.catalog = ProviderCatalog((replace(provider(), manifest_sha256='c' * 64),))
    with pytest.raises(PortRejected, match='untrusted_provider'):
        service.request_extension_invocation(call)
    assert not c.invocations and len(c.runs) == 2


def test_bridge_capability_revoked_while_queued_prevents_execution(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    grant = c.allow_invocation(job, report.report_ref, item)
    request = invocation(job, report, item, grant)
    operation = service.request_extension_invocation(request)

    bridge = c.bridges[SCOPE, BRIDGE]
    c.bridges[SCOPE, BRIDGE] = replace(bridge, capability_ids=())
    failed = c.finish_invocation(operation)
    receipt = service.read_invocation(operation)
    assert failed.status == 'failed' and failed.reason_code == 'capability_not_allowed'
    assert receipt.run is None and len(c.runs) == 2

    c.bridges[SCOPE, BRIDGE] = bridge
    assert service.request_extension_invocation(request) == operation
    assert c.finish_invocation(operation) == failed
    fresh_grant = c.allow_invocation(job, report.report_ref, item)
    fresh = service.request_extension_invocation(invocation(job, report, item, fresh_grant, key='new-approved-call'))
    assert c.finish_invocation(fresh).status == 'executed' and len(c.runs) == 3


def test_validation_timeout_never_authorizes_invocation(extension):
    service, c = extension
    _, request, job = submit(service, c)
    c.validation_elapsed_ms = c.scenarios[SCENARIO_ID].timeout_ms
    report = c.finish_validation(job)
    checked = service.read_validation(job, report.report_ref)
    (run,) = checked.candidate
    assert run.status == 'timeout' and run.reason_code == 'execution_timeout'
    assert run.judge_result == 'unknown' and run.exit_code is None and run.log_ref is None
    assert not report.comparable and report.verdict == 'not_comparable'
    item = c.register_input(COMPLETE)
    with pytest.raises(PortRejected, match='validation_required'):
        service.request_extension_invocation(invocation(job, report, item, request.execution_grant_ref))
    assert not c.invocations


def test_invocation_timeout_is_a_terminal_failure_with_a_run_receipt(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(MISSING)
    grant = c.allow_invocation(job, report.report_ref, item)
    operation = service.request_extension_invocation(invocation(job, report, item, grant))
    c.invocation_elapsed_ms = c.scenarios[SCENARIO_ID].timeout_ms
    view = c.finish_invocation(operation)
    receipt = service.read_invocation(operation)
    assert view.status == 'failed' and view.reason_code == 'execution_timeout'
    assert receipt.run.status == 'timeout' and receipt.run.provider_version == PROVIDER_VERSION
    assert receipt.run.fixture and receipt.run.log_ref is None
    c.invocation_elapsed_ms = 0
    assert c.finish_invocation(operation) == view and len(c.runs) == 3


@pytest.mark.parametrize('run_changes', [
    {},
    {'status': 'timeout', 'reason_code': 'execution_timeout', 'log_ref': None},
    {'status': 'timeout', 'reason_code': 'execution_timeout', 'judge_result': 'unknown'},
], ids=['executed-run', 'success-judgment', 'success-log'])
def test_timed_out_invocation_rejects_success_evidence(extension, run_changes):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    grant = c.allow_invocation(job, report.report_ref, item)
    operation = service.request_extension_invocation(invocation(job, report, item, grant))
    view = c.finish_invocation(operation)
    run = service.read_invocation(operation).run
    assert c.output(run)['ready'] is True

    c.operations[operation] = replace(view, status='failed', reason_code='execution_timeout')
    c.runs[job, run.run_ref] = replace(run, **run_changes)
    with pytest.raises(PortRejected, match='invalid_invocation_receipt'):
        service.read_invocation(operation)


@pytest.mark.parametrize('stage', ['validation', 'invocation'])
def test_timed_out_request_cannot_run_again_with_a_new_key_and_the_same_grant(extension, stage):
    service, c = extension
    _, request, job = submit(service, c)
    timeout = c.scenarios[SCENARIO_ID].timeout_ms
    if stage == 'validation':
        c.validation_elapsed_ms = timeout
        assert c.finish_validation(job).reason_code == 'execution_timeout'
        assert service.submit_extension(request) == job
        with pytest.raises(PortRejected, match='authorization_required'):
            service.submit_extension(replace(request, idempotency_key='retry-with-new-key'))
        assert len(c.jobs) == 1 and len(c.runs) == 2
    else:
        report = c.finish_validation(job)
        item = c.register_input(COMPLETE)
        grant = c.allow_invocation(job, report.report_ref, item)
        call = invocation(job, report, item, grant)
        operation = service.request_extension_invocation(call)
        c.invocation_elapsed_ms = timeout
        assert c.finish_invocation(operation).reason_code == 'execution_timeout'
        assert service.request_extension_invocation(call) == operation
        with pytest.raises(PortRejected, match='authorization_required'):
            service.request_extension_invocation(replace(call, idempotency_key='retry-with-new-key'))
        assert len(c.invocations) == 1 and len(c.runs) == 3


@pytest.mark.parametrize('stage', ['validation', 'invocation'])
def test_concurrent_completion_creates_only_one_set_of_run_receipts(extension, monkeypatch, stage):
    service, c = extension
    _, _, job = submit(service, c)
    if stage == 'validation':
        complete, target, expected = c.finish_validation, job, 2
    else:
        report = c.finish_validation(job)
        item = c.register_input(COMPLETE)
        grant = c.allow_invocation(job, report.report_ref, item)
        target = service.request_extension_invocation(invocation(job, report, item, grant))
        complete, expected = c.finish_invocation, 3

    running, release, duplicate, retried = Event(), Event(), Event(), Event()
    run = c._run
    target_phase = 'candidate' if stage == 'validation' else 'extension_invoke'

    def wait_for_retry(*args, **kwargs):
        if args[3] == target_phase:
            if running.is_set():
                duplicate.set()
            else:
                running.set()
                assert release.wait(5)
        return run(*args, **kwargs)

    def retry():
        retried.set()
        return complete(target)

    monkeypatch.setattr(c, '_run', wait_for_retry)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(complete, target)
        try:
            assert running.wait(5)
            second = executor.submit(retry)
            assert retried.wait(5)
            assert not duplicate.wait(0.25)
        finally:
            release.set()
        assert first.result(timeout=5) == second.result(timeout=5)
    assert len(c.runs) == expected


def test_runner_timeout_exception_is_terminal_and_has_no_result(extension, monkeypatch):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    grant = c.allow_invocation(job, report.report_ref, item)
    operation = service.request_extension_invocation(invocation(job, report, item, grant))
    attempts = []

    def timeout(self, **kwargs):
        attempts.append(self)
        raise TimeoutError

    monkeypatch.setattr(Inventory, 'check', timeout)
    final = c.finish_invocation(operation)
    assert c.finish_invocation(operation) == final and attempts == [COMPLETE]
    receipt = service.read_invocation(operation)
    assert receipt.operation.status == 'failed' and receipt.operation.reason_code == 'execution_timeout'
    assert receipt.run.status == 'timeout' and receipt.run.log_ref is None


def test_invocation_output_limit_does_not_publish_a_partial_result(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    required = tuple(f'docs/{index:02d}-' + 'a' * 180 + '.md' for index in range(32))
    item = c.register_input(Inventory(required, ()))
    grant = c.allow_invocation(job, report.report_ref, item)
    operation = service.request_extension_invocation(invocation(job, report, item, grant))
    c.finish_invocation(operation)
    receipt = service.read_invocation(operation)
    assert receipt.operation.status == 'failed' and receipt.operation.reason_code == 'output_limit'
    assert receipt.run.status == 'failed' and receipt.run.log_truncated and receipt.run.log_ref is None


def test_failed_report_cannot_be_reused_as_a_pass(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    grant = c.allow_invocation(job, report.report_ref, item)
    ref = report.candidate_run_refs[0]
    candidate = c.read_run(job, ref)
    c.runs[(job, ref)] = replace(candidate, judge_result='not_met')
    with pytest.raises(PortRejected, match='invalid_comparison_report'):
        service.read_validation(job, report.report_ref)
    with pytest.raises(PortRejected, match='validation_required'):
        service.request_extension_invocation(invocation(job, report, item, grant))
    assert not c.invocations
    another = replace(job, project_id=OTHER.project_id)
    with pytest.raises(PortRejected, match='out_of_scope'):
        service.read_validation(another, report.report_ref)


def test_invocation_rejects_a_substituted_run_even_when_its_references_are_unchanged(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    grant = c.allow_invocation(job, report.report_ref, item)
    ref = report.baseline_run_refs[0]
    c.runs[(job, ref)] = replace(c.read_run(job, ref), snapshot_sha256='d' * 64)
    with pytest.raises(PortRejected, match='validation_stale'):
        service.request_extension_invocation(invocation(job, report, item, grant))
    assert not c.invocations


@pytest.mark.parametrize('second_inventory', [MISSING, COMPLETE], ids=['different-input', 'same-input'])
def test_read_invocation_rejects_another_operations_run(extension, second_inventory):
    service, c = extension
    _, _, job, report = validated(service, c)
    first_item = c.register_input(COMPLETE)
    second_item = c.register_input(second_inventory)
    first = service.request_extension_invocation(
        invocation(job, report, first_item, c.allow_invocation(job, report.report_ref, first_item), key='first'),
    )
    second = service.request_extension_invocation(
        invocation(job, report, second_item, c.allow_invocation(job, report.report_ref, second_item), key='second'),
    )
    first_view = c.finish_invocation(first)
    second_view = c.finish_invocation(second)
    assert c.output(service.read_invocation(first).run)['ready'] is True
    assert c.output(service.read_invocation(second).run)['ready'] is (second_inventory == COMPLETE)

    c.operations[second] = replace(second_view, run_ref=first_view.run_ref)
    with pytest.raises(PortRejected, match='invalid_invocation_receipt'):
        service.read_invocation(second)


def test_invocation_run_must_match_the_approved_input_digest(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    item = c.register_input(COMPLETE)
    operation = service.request_extension_invocation(
        invocation(job, report, item, c.allow_invocation(job, report.report_ref, item)),
    )
    view = c.finish_invocation(operation)
    run = service.read_invocation(operation).run
    assert view.invocation_input_sha256 == run.snapshot_sha256 == item.sha256

    c.runs[job, run.run_ref] = replace(run, snapshot_sha256=c.register_input(MISSING).sha256)
    with pytest.raises(PortRejected, match='invalid_invocation_receipt'):
        service.read_invocation(operation)


def test_pass_label_does_not_override_a_timeout_reason(extension):
    service, c = extension
    _, _, job, report = validated(service, c)
    ref = report.candidate_run_refs[0]
    c.runs[(job, ref)] = replace(c.read_run(job, ref), reason_code='execution_timeout')
    with pytest.raises(PortRejected, match='invalid_comparison_report'):
        service.read_validation(job, report.report_ref)


@pytest.mark.parametrize('candidate_sha256', [None, 'a' * 64], ids=['missing-digest', 'different-digest'])
def test_pass_label_requires_a_digest_matching_the_executed_candidate(extension, candidate_sha256):
    service, c = extension
    _, _, job, report = validated(service, c)
    _, original_proof = c.validation_results[job]
    c.validation_results[job] = replace(report, candidate_sha256=candidate_sha256), original_proof
    with pytest.raises(PortRejected, match='invalid_comparison_report'):
        service.read_validation(job, report.report_ref)


def test_changed_payload_with_same_key_does_not_repeat_validation_or_invocation(extension):
    service, c = extension
    _, request, job, report = validated(service, c)
    with pytest.raises(PortRejected, match='idempotency_conflict'):
        service.submit_extension(replace(request, execution_grant_ref='changed-grant'))
    item = c.register_input(COMPLETE)
    grant = c.allow_invocation(job, report.report_ref, item)
    request = invocation(job, report, item, grant)
    op = service.request_extension_invocation(request)
    with pytest.raises(PortRejected, match='idempotency_conflict'):
        service.request_extension_invocation(replace(request, invocation_grant_ref='changed-grant'))
    assert service.request_extension_invocation(request) == op
    assert len(c.jobs) == len(c.invocations) == 1


@pytest.mark.parametrize('path', ['../outside.md', 'C:/secret.txt', '/outside.md'])
def test_fixture_inventory_accepts_only_virtual_relative_paths(path):
    with pytest.raises(ValueError, match='invalid fixture inventory'):
        Inventory(('README.md',), (path,))
