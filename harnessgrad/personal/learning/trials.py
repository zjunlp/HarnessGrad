"""Probe selected hosts and retain scoped trial results from C's receipts."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, replace
from sqlite3 import Connection

from harnessgrad.personal.contracts import decode, digest, encode, identifier
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.extension_contracts import ComparisonReport, RunRecord
from harnessgrad.personal.learning.maintenance_contracts import VersionReceipt
from harnessgrad.personal.learning.reflection_contracts import JobRef, PortRejected
from harnessgrad.personal.learning.store import LearningStore, SCOPE
from harnessgrad.personal.learning.trial_contracts import (
    CAPABILITIES, CapabilityCheck, CapabilityView, HostProbe, LoadObservation,
    TrialPort, TrialReceipt, TrialRecord, TrialRef,
)

TRIAL_EVIDENCE_VERSION = 2


def _prior_trial(db: Connection, scope: LearningScope, key: str, fingerprint: str) -> TrialRef | None:
    row = db.execute(f'SELECT id,request_sha256 FROM learning_trials WHERE {SCOPE} AND request_key=?',
                     (*scope.key(), key)).fetchone()
    if row is None:
        return None
    if row['request_sha256'] != fingerprint:
        raise PortRejected('idempotency_conflict')
    return TrialRef(row['id'])


class TrialService:
    """D5 reads evidence and records it without controlling host writes."""

    def __init__(self, store: LearningStore, port: TrialPort):
        self.store = store
        self.port = port

    def probe_host(self, scope: LearningScope, host_binding_ref: str) -> CapabilityView:
        return self._inspect(scope, host_binding_ref).view

    def compatibility_matrix(self, bindings: tuple[tuple[LearningScope, str], ...]) -> tuple[HostProbe, ...]:
        """Keep per-binding evidence, including fixture provenance, on every row."""
        return tuple(self._inspect(scope, ref) for scope, ref in bindings)

    def _inspect(self, scope: LearningScope, ref: str) -> HostProbe:
        probe = self.port.probe_host(scope, identifier(ref))
        binding, view = probe.binding, probe.view
        if binding.scope != scope or binding.host_binding_ref != ref or view.scope != scope:
            raise PortRejected('out_of_scope')
        if (binding.mode != view.mode or (view.host_version is not None and view.host_version != binding.host_version)
                or type(probe.fixture) is not bool):
            raise PortRejected('host_binding_mismatch')
        by_name = {check.capability: check for check in view.checks}
        if len(by_name) != len(view.checks):
            raise PortRejected('invalid_capability_view')
        checks = []
        for name in CAPABILITIES:
            check = by_name.get(name) or CapabilityCheck(name, 'unchecked', 'capability_unchecked')
            if view.host_version is None and check.status == 'ready':
                check = CapabilityCheck(name, 'unchecked', 'version_unchecked')
            checks.append(check)
        return replace(probe, view=replace(view, checks=tuple(checks)))

    def record_trial(self, record: TrialRecord) -> TrialRef:
        scope = record.scope
        if (record.job.profile_id, record.job.project_id) != scope.key():
            raise PortRejected('out_of_scope')
        fingerprint = digest(asdict(record))
        key = record.idempotency_key
        with closing(self.store.connection()) as db:
            if prior := _prior_trial(db, scope, key, fingerprint):
                return prior
        trial = TrialRef('trial_' + digest([*scope.key(), key]))
        details = self._collect(record, trial)
        with self.store.transaction() as db:
            if prior := _prior_trial(db, scope, key, fingerprint):
                return prior
            db.execute('INSERT INTO learning_trials VALUES (?,?,?,?,?,?)',
                       (*scope.key(), trial.trial_ref, key, fingerprint,
                        encode({**asdict(details), 'evidence_version': TRIAL_EVIDENCE_VERSION})))
        return trial

    def read_trial(self, scope: LearningScope, trial_ref: str) -> TrialReceipt:
        with closing(self.store.connection()) as db:
            row = db.execute(f'SELECT body FROM learning_trials WHERE {SCOPE} AND id=?',
                             (*scope.key(), identifier(trial_ref))).fetchone()
        if row is None:
            raise PortRejected('out_of_scope')
        body = decode(row['body'])
        if body.pop('evidence_version', None) != TRIAL_EVIDENCE_VERSION:
            # Preserve the stored receipt. Older summaries cannot prove these outcomes.
            body['comparison_verdict'] = None
            body['behavior_state'] = 'unchecked'
        record = body['record']
        record['scope'] = LearningScope(**record['scope'])
        record['job'] = JobRef(**record['job'])
        body['record'] = TrialRecord(**record)
        return TrialReceipt(**body)

    def _collect(self, record: TrialRecord, trial: TrialRef) -> TrialReceipt:
        # A completed job pins the binding used for its receipts. A new probe
        # describes current capabilities and can fail or report another version.
        job = self.port.read_trial_job(record.job)
        binding = job.binding
        if job.job != record.job or binding.scope != record.scope:
            raise PortRejected('out_of_scope')
        if (record.host_binding_ref, record.host_version, record.mode, record.os) != (
                binding.host_binding_ref, binding.host_version, binding.mode, binding.os):
            raise PortRejected('host_binding_mismatch')
        if job.scenario_id != record.scenario_id:
            raise PortRejected('scenario_mismatch')
        if job.fixture is not record.fixture:
            raise PortRejected('fixture_mismatch')
        calls = job.real_model_calls
        if calls is not None and (type(calls) is not int or calls < 0):
            raise PortRejected('invalid_trial_usage')
        comparison = self._comparison(record)
        receipt, version_status = self._version(record)
        if comparison is not None and receipt is not None and version_status == 'applied':
            if (not receipt.candidate_id or not receipt.candidate_sha256
                    or (comparison.candidate_id, comparison.candidate_sha256)
                    != (receipt.candidate_id, receipt.candidate_sha256)):
                raise PortRejected('candidate_mismatch')
        observation = self._observation(record, receipt)
        return TrialReceipt(trial.trial_ref, record, binding.host_id,
                            comparison.verdict if comparison else None, version_status,
                            observation.load_state if observation else 'unchecked',
                            observation.behavior_state if observation else 'unchecked', calls)

    def _run(self, record: TrialRecord, ref: str) -> RunRecord:
        run = self.port.read_run(record.job, ref)
        if run.job != record.job or run.run_ref != ref:
            raise PortRejected('out_of_scope')
        if run.scenario_id != record.scenario_id or run.fixture is not record.fixture:
            raise PortRejected('invalid_run_receipt')
        return run

    def _comparison(self, record: TrialRecord) -> ComparisonReport | None:
        if record.comparison_ref is None:
            return None
        report = self.port.read_comparison(record.job, record.comparison_ref)
        if report.job != record.job or report.report_ref != record.comparison_ref:
            raise PortRejected('out_of_scope')
        if report.fixture is not record.fixture:
            raise PortRejected('fixture_mismatch')
        refs = (*report.baseline_run_refs, *report.candidate_run_refs)
        if len(set(refs)) != len(refs):
            raise PortRejected('invalid_comparison_report')
        baseline = tuple(self._run(record, ref) for ref in report.baseline_run_refs)
        candidate = tuple(self._run(record, ref) for ref in report.candidate_run_refs)
        if any(run.phase != 'baseline' for run in baseline) or any(run.phase != 'candidate' for run in candidate):
            raise PortRejected('invalid_comparison_report')
        if any(not report.candidate_sha256 or run.prepared_sha256 != report.candidate_sha256 for run in candidate):
            raise PortRejected('invalid_comparison_report')
        if report.verdict == 'retest_passed' and (
                not report.comparable or not report.candidate_id or not baseline or not candidate
                or report.reason_code == 'execution_timeout'
                or any(run.status != 'executed' or run.reason_code == 'execution_timeout'
                       for run in (*baseline, *candidate))
                or any(run.judge_result != 'met' for run in candidate)
                or len({(run.scenario_sha256, run.snapshot_sha256) for run in (*baseline, *candidate)}) != 1):
            raise PortRejected('invalid_comparison_report')
        return report

    def _version(self, record: TrialRecord) -> tuple[VersionReceipt | None, str | None]:
        if record.version_receipt_ref is None:
            return None, None
        receipt = self.port.read_version_receipt(record.job, record.version_receipt_ref)
        if receipt.operation.job != record.job or receipt.receipt_ref != record.version_receipt_ref:
            raise PortRejected('out_of_scope')
        view = self.port.read_operation(receipt.operation)
        if (view.operation != receipt.operation or view.version_receipt_ref != receipt.receipt_ref
                or (view.kind, view.status) not in (('apply', 'applied'), ('rollback', 'rolled_back'))
                or receipt.after_version_ref is None or not receipt.entries
                or len({entry.target_id for entry in receipt.entries}) != len(receipt.entries)
                or any(entry.status not in (view.status, 'unchanged')
                       or (entry.status == 'unchanged' and entry.before_sha256 != entry.after_sha256)
                       for entry in receipt.entries)):
            raise PortRejected('invalid_version_receipt')
        return receipt, view.status

    def _observation(self, record: TrialRecord, receipt: VersionReceipt | None) -> LoadObservation | None:
        if record.observation_ref is None:
            return None
        observation = self.port.read_load_observation(record.job, record.observation_ref)
        if observation.job != record.job or observation.observation_ref != record.observation_ref:
            raise PortRejected('out_of_scope')
        if (receipt is None or observation.version_receipt_ref != receipt.receipt_ref
                or (observation.host_version, observation.mode) != (record.host_version, record.mode)
                or observation.fixture is not record.fixture
                or observation.load_state not in ('loaded', 'not_loaded', 'unchecked', 'unavailable')):
            raise PortRejected('invalid_load_observation')
        if observation.load_state == 'loaded' and (
                not observation.new_thread_ref or not observation.evidence_ref
                or observation.loaded_version_ref != receipt.after_version_ref):
            raise PortRejected('invalid_load_observation')
        if observation.behavior_state != 'unchecked':
            if (observation.load_state != 'loaded' or observation.behavior_state not in ('met', 'not_met')
                    or not observation.behavior_run_ref):
                raise PortRejected('invalid_behavior_observation')
            run = self._run(record, observation.behavior_run_ref)
            if (run.phase != 'post_load_behavior' or run.operation != receipt.operation
                    or run.new_thread_ref != observation.new_thread_ref
                    or run.loaded_version_ref != observation.loaded_version_ref
                    or run.status != 'executed' or run.reason_code == 'execution_timeout'
                    or run.judge_result != observation.behavior_state):
                raise PortRejected('invalid_behavior_observation')
        return observation
