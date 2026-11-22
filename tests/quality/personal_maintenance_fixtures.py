"""Shared fake C version service for personal maintenance and trial fixtures."""

from harnessgrad.personal.contracts import digest
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.maintenance_contracts import (
    CurrentVersion, OperationRef, OperationView, VersionEntry, VersionReceipt,
)
from harnessgrad.personal.learning.reflection_contracts import (
    ArtifactRef, ArtifactView, CandidateRequest, JobRef, PortRejected, SnapshotRef,
)

SCOPE = LearningScope('profile', 'selected-project')
TARGET = 'project-instructions'
PATH = 'instruction.md'
BEFORE = '# Project rules\r\n'


class FixtureC:
    """Synthetic version service, with explicitly granted rollback and no filesystem writes."""

    def __init__(self, scope=SCOPE, initial=BEFORE):
        self.scope = scope
        self.files = {PATH: initial}
        self.version = 'version-0'
        self.packages = {self.version: dict(self.files)}
        self.snapshots = {}
        self.candidates = {}
        self.jobs = {}
        self.operations = {}
        self.receipts = {}
        self.rollbacks = {}
        self.grants = set()
        self.rollback_calls = []
        self.force_conflict = False
        self.drop_rollback_ack = False

    def check_scope(self, scope):
        if scope != self.scope:
            raise PortRejected('out_of_scope')

    def check_job(self, job):
        self.read_job_candidate(job)

    def read_job_candidate(self, job):
        self.check_scope(LearningScope(job.profile_id, job.project_id))
        candidate = next((candidate for candidate, ref in self.jobs.values() if ref == job), None)
        if candidate is None:
            raise PortRejected('out_of_scope')
        return candidate

    def freeze_snapshot(self, scope, targets):
        self.check_scope(scope)
        assert targets == (TARGET,)
        fingerprint = digest([*scope.key(), self.version, self.files])
        ref = SnapshotRef('snapshot_' + fingerprint, fingerprint, targets)
        self.snapshots[ref.snapshot_ref] = ArtifactView(ref.snapshot_ref, fingerprint, 'virtual_snapshot',
                                                       {'files': dict(self.files)})
        return ref

    def read_artifact(self, scope, ref, *, kind):
        self.check_scope(scope)
        registry = self.snapshots if kind == 'virtual_snapshot' else self.candidates
        artifact = registry.get(ref)
        if artifact is None or artifact.kind != kind:
            raise PortRejected('out_of_scope')
        return artifact

    def register_candidate(self, scope, snapshot_ref, files):
        self.read_artifact(scope, snapshot_ref, kind='virtual_snapshot')
        assert files.keys() == self.files.keys()
        body = {'files': dict(files)}
        sha = digest(body)
        ref = 'candidate_' + sha
        self.candidates[ref] = ArtifactView(ref, sha, 'text_candidate', body)
        return ArtifactRef(ref, sha, 'text_candidate')

    def submit_candidate(self, candidate):
        self.check_scope(LearningScope(candidate.profile_id, candidate.project_id))
        if prior := self.jobs.get(candidate.idempotency_key):
            if prior[0] != candidate:
                raise PortRejected('idempotency_conflict')
            return prior[1]
        assert self.candidates[candidate.candidate_ref].sha256 == candidate.candidate_sha256
        job = JobRef(*self.scope.key(), f'job-{len(self.jobs) + 1}')
        self.jobs[candidate.idempotency_key] = candidate, job
        return job

    def read_current_versions(self, scope, target_ids):
        return self.read_version_targets(scope, self.version, target_ids)

    def read_version_targets(self, scope, version_ref, target_ids):
        self.check_scope(scope)
        assert target_ids == (TARGET,)
        if version_ref not in self.packages:
            raise PortRejected('version_unverified')
        return (CurrentVersion(TARGET, 'target-' + version_ref, digest(self.packages[version_ref][PATH])),)

    def external_change(self, body):
        self._replace({PATH: body})

    def _replace(self, files):
        if files != self.files:
            self.files = dict(files)
            self.version = f'version-{len(self.packages)}'
            self.packages[self.version] = self.files

    def apply(self, job):
        candidate = self.read_job_candidate(job)
        op = OperationRef(job, f'apply-{job.job_id}')
        if op not in self.operations:
            self._complete(op, 'apply', 'applied', self.candidates[candidate.candidate_ref].payload['files'],
                           candidate)
        return op

    def request_rollback(self, request):
        self.check_job(request.job)
        self.rollback_calls.append(request)
        if prior := self.rollbacks.get(request.idempotency_key):
            if prior[0] != request:
                raise PortRejected('idempotency_conflict')
            return prior[1]
        if request.authorization_ref not in self.grants:
            raise PortRejected('authorization_required')
        if self.force_conflict or request.expected_versions != self.read_current_versions(self.scope, (TARGET,)):
            raise PortRejected('rollback_conflict')
        assert request.restore_version_ref in self.packages
        op = OperationRef(request.job, 'rollback-' + request.job.job_id)
        self.rollbacks[request.idempotency_key] = request, op
        self.operations[op] = OperationView(op, 'rollback', 'queued')
        if self.drop_rollback_ack:
            self.drop_rollback_ack = False
            raise ConnectionError('lost rollback acknowledgement')
        return op

    def finish_rollback(self, operation):
        if self.read_operation(operation).status != 'queued':
            return
        (request,) = (request for request, op in self.rollbacks.values() if op == operation)
        if request.authorization_ref not in self.grants:
            self.operations[operation] = OperationView(operation, 'rollback', 'failed', 'authorization_required')
            return
        if request.expected_versions != self.read_current_versions(self.scope, (TARGET,)):
            self.operations[operation] = OperationView(operation, 'rollback', 'rollback_conflict')
            return
        self._complete(operation, 'rollback', 'rolled_back', self.packages[request.restore_version_ref])

    def _complete(self, operation, kind, outcome, files, candidate: CandidateRequest | None = None):
        old = self.files[PATH]
        prior = self.version
        self._replace(files)
        ref = f'receipt-{operation.operation_id}'
        status = outcome if old != self.files[PATH] else 'unchanged'
        entry = VersionEntry(TARGET, digest(old), digest(self.files[PATH]), status)
        self.receipts[(operation.job, ref)] = VersionReceipt(
            ref, operation, candidate.candidate_ref if candidate else None,
            candidate.candidate_sha256 if candidate else None, prior, self.version, None, (entry,),
        )
        self.operations[operation] = OperationView(operation, kind, outcome, version_receipt_ref=ref)

    def read_operation(self, operation):
        self.check_job(operation.job)
        view = self.operations.get(operation)
        if view is None:
            raise PortRejected('out_of_scope')
        return view

    def read_version_receipt(self, job, ref):
        self.check_job(job)
        receipt = self.receipts.get((job, ref))
        if receipt is None:
            raise PortRejected('out_of_scope')
        return receipt
