"""Admit declarative extension candidates and delegate execution to C."""
from __future__ import annotations

from harnessgrad.personal.contracts import digest, identifier
from harnessgrad.personal.learning.extension_contracts import (
    ExtensionInvocation, ExtensionInvocationRequest, ExtensionPort, ExtensionRequest, ExtensionSpec,
    ExtensionValidation, RunRecord, TrustedProvider,
)
from harnessgrad.personal.learning.maintenance_contracts import OperationRef
from harnessgrad.personal.learning.reflection_contracts import ArtifactRef, JobRef, PortRejected

EXTENSION_KIND = 'extension_candidate'


def _completed(run: RunRecord) -> bool:
    return run.status == 'executed' and run.reason_code != 'execution_timeout'


class ProviderCatalog:
    """An explicit allowlist for each profile and project."""

    def __init__(self, providers: tuple[TrustedProvider, ...]):
        self._providers = {(provider.scope, provider.provider_id, provider.provider_version): provider
                           for provider in providers}
        if len(self._providers) != len(providers):
            raise ValueError('duplicate trusted extension provider version')

    def check(self, request: ExtensionSpec) -> None:
        trusted = self._providers.get((request.scope, request.provider_id, request.provider_version))
        if trusted is None or (trusted.kind, trusted.manifest_sha256) != (request.kind, request.manifest_sha256):
            raise PortRejected('untrusted_provider')
        requested = set(request.capability_ids)
        if not requested <= set(trusted.capability_ids):
            raise PortRejected('capability_not_allowed')
        if not set(trusted.required_capability_ids) <= requested:
            raise PortRejected('capability_required')
        if request.scenario_id not in trusted.scenario_ids:
            raise PortRejected('scenario_unavailable')


class ExtensionService:
    """D4 client; a C acknowledgement is not evidence of a completed run."""

    def __init__(self, catalog: ProviderCatalog, port: ExtensionPort):
        self.catalog = catalog
        self.port = port

    def propose_extension(self, request: ExtensionSpec) -> ArtifactRef:
        self.catalog.check(request)
        artifact = self.port.register_extension_candidate(request)
        if artifact.kind != EXTENSION_KIND or artifact.sha256 != digest(request.as_dict()):
            raise PortRejected('invalid_candidate_artifact')
        identifier(artifact.ref)
        return artifact

    def submit_extension(self, request: ExtensionRequest) -> JobRef:
        # C validates grants and capabilities on first submission and owns idempotent replay.
        job = self.port.submit_extension(request)
        if (job.profile_id, job.project_id) != request.scope.key():
            raise PortRejected('out_of_scope')
        identifier(job.job_id)
        return job

    def request_extension_invocation(self, request: ExtensionInvocationRequest) -> OperationRef:
        operation = self.port.request_extension_invocation(request)
        if operation.job != request.job:
            raise PortRejected('out_of_scope')
        identifier(operation.operation_id)
        return operation

    def _read_run(self, job: JobRef, ref: str, phase: str) -> RunRecord:
        run = self.port.read_run(job, identifier(ref))
        if run.job != job or run.run_ref != ref:
            raise PortRejected('out_of_scope')
        if run.phase != phase:
            raise PortRejected('invalid_run_receipt')
        return run

    def read_validation(self, job: JobRef, report_ref: str) -> ExtensionValidation:
        report = self.port.read_comparison(job, identifier(report_ref))
        if report.job != job or report.report_ref != report_ref:
            raise PortRejected('out_of_scope')
        refs = (*report.baseline_run_refs, *report.candidate_run_refs)
        if len(set(refs)) != len(refs):
            raise PortRejected('invalid_comparison_report')
        baseline = tuple(self._read_run(job, ref, 'baseline') for ref in report.baseline_run_refs)
        candidate = tuple(self._read_run(job, ref, 'candidate') for ref in report.candidate_run_refs)
        runs = (*baseline, *candidate)
        if any(run.fixture != report.fixture for run in runs):
            raise PortRejected('invalid_comparison_report')
        if report.verdict == 'retest_passed' and (
                not report.comparable or not report.candidate_id or not report.candidate_sha256
                or not baseline or not candidate
                or report.reason_code == 'execution_timeout' or any(not _completed(run) for run in runs)
                or any(run.judge_result != 'met' or not run.provider_version
                       or run.prepared_sha256 != report.candidate_sha256
                       for run in candidate)
                or len({run.provider_version for run in candidate}) != 1
                or len({(run.scenario_id, run.scenario_sha256, run.snapshot_sha256) for run in runs}) != 1):
            raise PortRejected('invalid_comparison_report')
        return ExtensionValidation(report, baseline, candidate)

    def read_invocation(self, operation: OperationRef) -> ExtensionInvocation:
        view = self.port.read_operation(operation)
        if view.operation != operation:
            raise PortRejected('out_of_scope')
        if view.kind != 'extension_invoke':
            raise PortRejected('invalid_invocation_receipt')
        run = self._read_run(operation.job, view.run_ref, 'extension_invoke') if view.run_ref else None
        if run is not None and (run.operation != operation or not view.invocation_input_sha256
                                or run.snapshot_sha256 != view.invocation_input_sha256):
            raise PortRejected('invalid_invocation_receipt')
        if run is not None and (view.status in ('queued', 'running')
                                or (run.status == 'executed' and view.status != 'executed')):
            raise PortRejected('invalid_invocation_receipt')
        if view.status == 'executed' and (run is None or not _completed(run) or not run.provider_version
                                          or view.reason_code == 'execution_timeout'):
            raise PortRejected('invalid_invocation_receipt')
        if run is not None:
            timed_out = run.status == 'timeout' or 'execution_timeout' in (view.reason_code, run.reason_code)
            if timed_out and (run.status != 'timeout' or run.judge_result != 'unknown' or run.log_ref is not None):
                raise PortRejected('invalid_invocation_receipt')
        return ExtensionInvocation(view, run)
