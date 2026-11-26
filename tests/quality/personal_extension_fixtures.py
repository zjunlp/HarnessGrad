"""An offline C port and fixed delivery-check fixture. It only reads in-memory inventories."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from itertools import count
from threading import Lock
from typing import Callable

from harnessgrad.improvement.contracts import is_relative_path
from harnessgrad.personal.contracts import decode, digest, encode
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.extension_contracts import (
    ComparisonReport, ExtensionInvocationRequest, ExtensionRequest, ExtensionSpec, RunRecord, TrustedProvider,
)
from harnessgrad.personal.learning.extensions import EXTENSION_KIND, ProviderCatalog
from harnessgrad.personal.learning.maintenance_contracts import OperationRef, OperationView
from harnessgrad.personal.learning.reflection_contracts import ArtifactRef, JobRef, PortRejected

SCOPE = LearningScope('fixture-profile', 'fixture-project')
OTHER = LearningScope('fixture-profile', 'other-project')
PROVIDER_ID = 'delivery-inventory-check'
PROVIDER_VERSION = '1.0.0'
CAPABILITY = 'inventory.read'
SCENARIO_ID = 'pre-delivery-file-check'
BRIDGE = 'trusted-fixture-bridge'
EVIDENCE = 'selected-delivery-requirement'
MANIFEST_SHA256 = digest({
    'kind': 'workflow', 'provider_id': PROVIDER_ID, 'version': PROVIDER_VERSION,
    'capability_ids': [CAPABILITY], 'check': 'required inventory entries must be present',
})


@dataclass(frozen=True)
class Inventory:
    required: tuple[str, ...]
    present: tuple[str, ...]

    def __post_init__(self):
        for paths in (self.required, self.present):
            if (not isinstance(paths, tuple) or len(paths) > 128
                    or any(not is_relative_path(path) or len(path.encode('utf-8')) > 240 for path in paths)
                    or len(set(paths)) != len(paths)):
                raise ValueError('invalid fixture inventory paths')
        if not self.required:
            raise ValueError('fixture requires a nonempty delivery manifest')

    def check(self, *, checker_enabled: bool = True) -> dict:
        present = set(self.present)
        missing = [path for path in self.required if path not in present] if checker_enabled else []
        return {'ready': not missing, 'missing_files': missing}


REQUIRED = ('README.md', 'docs/usage.md')
COMPLETE = Inventory(REQUIRED, REQUIRED)
MISSING = Inventory(REQUIRED, ('README.md',))


@dataclass(frozen=True)
class Case:
    name: str
    inventory: Inventory
    expected_missing: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryScenario:
    cases: tuple[Case, ...] = (Case('complete', COMPLETE, ()), Case('missing', MISSING, ('docs/usage.md',)))
    runner_version: str = 'fixture-inventory-v1'
    judge_version: str = 'exact-missing-set-v1'
    timeout_ms: int = 1000
    max_output_bytes: int = 4096

    @property
    def sha256(self) -> str:
        return digest(asdict(self))

    def check(self, *, checker_enabled: bool) -> tuple[dict, str]:
        results = {case.name: case.inventory.check(checker_enabled=checker_enabled) for case in self.cases}
        matches = all(results[case.name]['missing_files'] == list(case.expected_missing)
                      and results[case.name]['ready'] == (not case.expected_missing) for case in self.cases)
        return {'cases': results}, 'met' if matches else 'not_met'


def provider(scope: LearningScope = SCOPE) -> TrustedProvider:
    return TrustedProvider(scope, 'workflow', PROVIDER_ID, PROVIDER_VERSION, MANIFEST_SHA256,
                           (CAPABILITY,), (SCENARIO_ID,), required_capability_ids=(CAPABILITY,))


def candidate_spec() -> ExtensionSpec:
    return ExtensionSpec(SCOPE, 'workflow', PROVIDER_ID, PROVIDER_VERSION, MANIFEST_SHA256,
                         (CAPABILITY,), SCENARIO_ID, (EVIDENCE,))


@dataclass(frozen=True)
class ValidationBinding:
    scope: LearningScope
    candidate_ref: str
    candidate_sha256: str
    scenario_sha256: str


@dataclass(frozen=True)
class InvocationBinding:
    validation: ValidationBinding
    job: JobRef
    validation_sha256: str
    input_ref: str
    input_sha256: str
    host_binding_ref: str


@dataclass(frozen=True)
class BridgePolicy:
    provider: tuple[str, str, str]
    capability_ids: tuple[str, ...]


class FixtureC:
    """The fixture owns all grants and outcomes, independently of the D client."""

    def __init__(self):
        self.catalog = ProviderCatalog((provider(),))
        self.scenarios = {SCENARIO_ID: DeliveryScenario()}
        self.capabilities = {'extension_execution', 'extension_invoke'}
        self.bridges = {(SCOPE, BRIDGE): BridgePolicy((PROVIDER_ID, PROVIDER_VERSION, MANIFEST_SHA256), (CAPABILITY,))}
        self.evidence = {SCOPE: {EVIDENCE}}
        self.candidates = {}
        self.inputs = {}
        self.execution_grants = {}
        self.invocation_grants = {}
        self._grant_ids = count(1)
        self._used_grants = set()
        self._lock = Lock()
        self.jobs = {}
        self.validation_results = {}
        self.operations = {}
        self.invocations = {}
        self.submissions = {}
        self.invocation_replays = {}
        self.runs = {}
        self.logs = {}
        self.validation_elapsed_ms = 1
        self.invocation_elapsed_ms = 1

    def _evidence(self, spec: ExtensionSpec) -> None:
        if not set(spec.evidence_refs) <= self.evidence.get(spec.scope, set()):
            raise PortRejected('out_of_scope')

    def register_extension_candidate(self, request: ExtensionSpec) -> ArtifactRef:
        self.catalog.check(request)
        self._evidence(request)
        sha = digest(request.as_dict())
        artifact = ArtifactRef('extension_' + sha, sha, EXTENSION_KIND)
        self.candidates[(request.scope, artifact.ref)] = artifact, request
        return artifact

    def register_input(self, inventory: Inventory, scope: LearningScope = SCOPE) -> ArtifactRef:
        sha = digest(asdict(inventory))
        item = ArtifactRef('inventory_' + sha, sha, 'fixture_inventory')
        self.inputs[(scope, item.ref)] = inventory
        return item

    def _validation(self, scope: LearningScope, ref: str, sha: str):
        selected = self.candidates.get((scope, ref))
        if selected is None:
            raise PortRejected('out_of_scope')
        artifact, spec = selected
        if (spec.scope != scope or artifact.ref != ref or artifact.kind != EXTENSION_KIND
                or artifact.sha256 != sha or digest(spec.as_dict()) != sha):
            raise PortRejected('candidate_mismatch')
        self.catalog.check(spec)
        self._evidence(spec)
        scenario = self.scenarios.get(spec.scenario_id)
        if scenario is None:
            raise PortRejected('scenario_unavailable')
        return ValidationBinding(scope, ref, sha, scenario.sha256), spec, scenario

    def allow_validation(self, artifact: ArtifactRef) -> str:
        binding, _, _ = self._validation(SCOPE, artifact.ref, artifact.sha256)
        key = f'isolated-grant-{next(self._grant_ids)}'
        self.execution_grants[key] = binding
        return key

    def _check_validation(self, request: ExtensionRequest):
        if 'extension_execution' not in self.capabilities:
            raise PortRejected('runner_unavailable')
        binding, spec, scenario = self._validation(request.scope, request.candidate_ref, request.candidate_sha256)
        if self.execution_grants.get(request.execution_grant_ref) != binding:
            raise PortRejected('authorization_required')
        return binding, spec, scenario

    def submit_extension(self, request: ExtensionRequest) -> JobRef:
        with self._lock:
            key = (request.scope, request.idempotency_key)
            if record := self.submissions.get(key):
                submitted, job = record
                if request != submitted:
                    raise PortRejected('idempotency_conflict')
                return job
            self._check_validation(request)
            self._consume(request.execution_grant_ref)
            job = JobRef(*request.scope.key(), f'extension-job-{len(self.jobs) + 1}')
            self.jobs[job] = request
            self.submissions[key] = request, job
            return job

    def _consume(self, ref: str) -> None:
        if ref in self._used_grants:
            raise PortRejected('authorization_required')
        self._used_grants.add(ref)

    def finish_validation(self, job: JobRef) -> ComparisonReport:
        with self._lock:
            request = self.jobs.get(job)
            if request is None:
                raise PortRejected('out_of_scope')
            if previous := self.validation_results.get(job):
                return previous[0]
            report = self._complete_validation(job, request)
            self.validation_results[job] = report, self._proof_sha256(report)
            return report

    def _complete_validation(self, job: JobRef, request: ExtensionRequest) -> ComparisonReport:
        baseline = candidate = ()
        verdict, reason = 'not_comparable', None
        try:
            binding, spec, scenario = self._check_validation(request)
        except PortRejected as exc:
            reason = exc.reason_code
        else:
            fixture_input = digest([asdict(case.inventory) for case in scenario.cases])
            baseline_run = self._run(
                job, spec, scenario, 'baseline', fixture_input, 0, None,
                lambda: scenario.check(checker_enabled=False),
            )
            candidate_run = self._run(
                job, spec, scenario, 'candidate', fixture_input, self.validation_elapsed_ms,
                binding.candidate_sha256, lambda: scenario.check(checker_enabled=True),
            )
            baseline, candidate = (baseline_run.run_ref,), (candidate_run.run_ref,)
            if baseline_run.status == candidate_run.status == 'executed':
                verdict = 'retest_passed' if candidate_run.judge_result == 'met' else 'retest_failed'
            else:
                reason = candidate_run.reason_code or baseline_run.reason_code
        return ComparisonReport(
            f'comparison-{job.job_id}', job, request.candidate_ref, baseline, candidate,
            verdict != 'not_comparable', verdict, reason, True, candidate_sha256=request.candidate_sha256,
        )

    def _proof_sha256(self, report: ComparisonReport) -> str:
        refs = (*report.baseline_run_refs, *report.candidate_run_refs)
        return digest({'report': asdict(report), 'runs': [asdict(self.read_run(report.job, ref)) for ref in refs]})

    def _invocation(self, job: JobRef, report_ref: str, item: ArtifactRef, bridge: str):
        if 'extension_invoke' not in self.capabilities:
            raise PortRejected('unavailable')
        report = self.read_comparison(job, report_ref)
        if (report.verdict != 'retest_passed' or not report.comparable or not report.fixture
                or not report.baseline_run_refs or not report.candidate_run_refs):
            raise PortRejected('validation_required')
        submitted = self.jobs[job]
        binding, spec, scenario = self._validation(submitted.scope, submitted.candidate_ref, submitted.candidate_sha256)
        runs = tuple(self.read_run(job, ref) for ref in report.candidate_run_refs)
        baseline = tuple(self.read_run(job, ref) for ref in report.baseline_run_refs)
        if (any(not run.fixture or run.status != 'executed' for run in (*baseline, *runs))
                or any(run.phase != 'baseline' for run in baseline)
                or any(run.phase != 'candidate' or run.judge_result != 'met' for run in runs)):
            raise PortRejected('validation_required')
        if report.candidate_id != binding.candidate_ref or any(
                (run.scenario_sha256, run.prepared_sha256, run.provider_version)
                != (scenario.sha256, binding.candidate_sha256, spec.provider_version) for run in runs):
            raise PortRejected('validation_stale')
        proof_sha256 = self._proof_sha256(report)
        _, original_sha256 = self.validation_results[job]
        if proof_sha256 != original_sha256:
            raise PortRejected('validation_stale')
        policy = self.bridges.get((submitted.scope, bridge))
        if policy is None or policy.provider != (spec.provider_id, spec.provider_version, spec.manifest_sha256):
            raise PortRejected('untrusted_bridge')
        if not set(spec.capability_ids) <= set(policy.capability_ids):
            raise PortRejected('capability_not_allowed')
        inventory = self.inputs.get((submitted.scope, item.ref))
        if inventory is None:
            raise PortRejected('out_of_scope')
        if digest(asdict(inventory)) != item.sha256:
            raise PortRejected('input_mismatch')
        frozen = InvocationBinding(binding, job, proof_sha256, item.ref, item.sha256, bridge)
        return frozen, spec, scenario, inventory

    def allow_invocation(self, job: JobRef, report_ref: str, item: ArtifactRef, bridge: str = BRIDGE) -> str:
        binding, _, _, _ = self._invocation(job, report_ref, item, bridge)
        key = f'invocation-grant-{next(self._grant_ids)}'
        self.invocation_grants[key] = binding
        return key

    def _check_invocation(self, request: ExtensionInvocationRequest):
        item = ArtifactRef(request.input_ref, request.input_sha256, 'fixture_inventory')
        bound = self._invocation(request.job, request.report_ref, item, request.host_binding_ref)
        if self.invocation_grants.get(request.invocation_grant_ref) != bound[0]:
            raise PortRejected('authorization_required')
        return bound

    def request_extension_invocation(self, request: ExtensionInvocationRequest) -> OperationRef:
        with self._lock:
            key = (request.job, request.idempotency_key)
            if record := self.invocation_replays.get(key):
                submitted, operation = record
                if request != submitted:
                    raise PortRejected('idempotency_conflict')
                return operation
            self._check_invocation(request)
            self._consume(request.invocation_grant_ref)
            operation = OperationRef(request.job, f'invocation-{len(self.invocations) + 1}')
            self.invocation_replays[key] = request, operation
            self.invocations[operation] = request
            self.operations[operation] = OperationView(
                operation, 'extension_invoke', 'queued', invocation_input_sha256=request.input_sha256,
            )
            return operation

    def finish_invocation(self, operation: OperationRef) -> OperationView:
        with self._lock:
            previous = self.read_operation(operation)
            if previous.status != 'queued':
                return previous
            view = self._complete_invocation(previous, self.invocations[operation])
            self.operations[operation] = view
            return view

    def _complete_invocation(self, view: OperationView, request: ExtensionInvocationRequest) -> OperationView:
        try:
            binding, spec, scenario, inventory = self._check_invocation(request)
        except PortRejected as exc:
            return replace(view, status='failed', reason_code=exc.reason_code)

        def check_input():
            result = inventory.check()
            return result, 'met' if result['ready'] else 'not_met'

        run = self._run(
            request.job, spec, scenario, 'extension_invoke', binding.input_sha256,
            self.invocation_elapsed_ms, binding.validation.candidate_sha256, check_input,
            operation=view.operation,
        )
        status = 'executed' if run.status == 'executed' else 'failed'
        return replace(view, status=status, reason_code=run.reason_code, run_ref=run.run_ref)

    def _run(
        self, job: JobRef, spec: ExtensionSpec, scenario: DeliveryScenario, phase: str,
        input_sha256: str, elapsed_ms: int, candidate_sha256: str | None,
        execute: Callable[[], tuple[dict, str]], *, operation: OperationRef | None = None,
    ) -> RunRecord:
        ref = f'run-{len(self.runs) + 1}'
        log_ref, truncated = None, False
        try:
            if elapsed_ms >= scenario.timeout_ms:
                raise TimeoutError
            result, judge = execute()
        except TimeoutError:
            status, judge, reason = 'timeout', 'unknown', 'execution_timeout'
        else:
            encoded = encode(result)
            if len(encoded.encode('utf-8')) > scenario.max_output_bytes:
                status, judge, reason = 'failed', 'unknown', 'output_limit'
                truncated = True
            else:
                status, reason = 'executed', None
                log_ref = f'log-{ref}'
                self.logs[(job, log_ref)] = encoded
        run = RunRecord(
            ref, job, spec.scenario_id, phase, 1, scenario.sha256, input_sha256, candidate_sha256,
            spec.provider_version if candidate_sha256 is not None else None, (), None, log_ref, truncated,
            status, judge, reason, True, operation=operation,
        )
        self.runs[(job, ref)] = run
        return run

    def read_operation(self, operation: OperationRef) -> OperationView:
        result = self.operations.get(operation)
        if result is None:
            raise PortRejected('out_of_scope')
        return result

    def read_comparison(self, job: JobRef, report_ref: str) -> ComparisonReport:
        proof = self.validation_results.get(job)
        if proof is None or proof[0].report_ref != report_ref:
            raise PortRejected('out_of_scope')
        return proof[0]

    def read_run(self, job: JobRef, run_ref: str) -> RunRecord:
        run = self.runs.get((job, run_ref))
        if run is None:
            raise PortRejected('out_of_scope')
        return run

    def output(self, run: RunRecord) -> dict:
        return decode(self.logs[(run.job, run.log_ref)])
