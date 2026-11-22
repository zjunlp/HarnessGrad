"""Scoped host probes and receipts for continuous learning trials."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from harnessgrad.personal.contracts import identifier
from harnessgrad.personal.learning.contracts import LearningScope
from harnessgrad.personal.learning.extension_contracts import ComparisonReport, RunRecord
from harnessgrad.personal.learning.maintenance_contracts import OperationRef, OperationView, VersionReceipt
from harnessgrad.personal.learning.reflection_contracts import JobRef

CAPABILITIES = (
    'selected_read', 'render', 'retest', 'apply', 'rollback', 'observe_load',
    'daily_source', 'extension_execution', 'extension_invoke',
)
HostMode = Literal['controlled', 'bypass']
CapabilityStatus = Literal['ready', 'unavailable', 'refused', 'unchecked']


@dataclass(frozen=True)
class SelectedHost:
    """A projection of the trusted host binding, with no local filesystem path."""

    scope: LearningScope
    host_binding_ref: str
    host_id: str
    host_version: str | None
    mode: HostMode
    os: str

    def __post_init__(self):
        for value in (self.host_binding_ref, self.host_id, self.os):
            identifier(value)
        if self.host_version is not None:
            identifier(self.host_version)
        if self.mode not in ('controlled', 'bypass'):
            raise ValueError('invalid host access mode')


@dataclass(frozen=True)
class CapabilityCheck:
    capability: str
    status: CapabilityStatus
    reason_code: str | None = None

    def __post_init__(self):
        if self.capability not in CAPABILITIES or self.status not in ('ready', 'unavailable', 'refused', 'unchecked'):
            raise ValueError('invalid host capability check')
        if self.reason_code is not None:
            identifier(self.reason_code)


@dataclass(frozen=True)
class CapabilityView:
    scope: LearningScope
    host_version: str | None
    mode: HostMode
    checks: tuple[CapabilityCheck, ...]


@dataclass(frozen=True)
class HostProbe:
    """One binding's checks. Fixture rows describe the synthetic adapter only."""

    binding: SelectedHost
    view: CapabilityView
    fixture: bool

    @property
    def fallback(self) -> Literal['manual_import', 'diff_export'] | None:
        """Fallback for the retest and adoption flow, independent of load checks."""
        ready = {check.capability for check in self.view.checks if check.status == 'ready'}
        if self.view.host_version is None or not {'selected_read', 'render'} <= ready:
            return 'manual_import'
        if not {'retest', 'apply'} <= ready:
            return 'diff_export'
        return None


@dataclass(frozen=True)
class TrialRecord:
    scope: LearningScope
    host_binding_ref: str
    host_version: str
    mode: HostMode
    os: str
    scenario_id: str
    round: int
    job: JobRef
    fixture: bool
    idempotency_key: str
    comparison_ref: str | None = None
    version_receipt_ref: str | None = None
    observation_ref: str | None = None

    def __post_init__(self):
        for value in (self.host_binding_ref, self.host_version, self.os, self.scenario_id,
                      self.idempotency_key, self.job.job_id):
            identifier(value)
        for value in (self.comparison_ref, self.version_receipt_ref, self.observation_ref):
            if value is not None:
                identifier(value)
        if (self.mode not in ('controlled', 'bypass') or type(self.fixture) is not bool
                or type(self.round) is not int or not 1 <= self.round < 2**31):
            raise ValueError('invalid trial mode, fixture flag or round')


@dataclass(frozen=True)
class TrialRef:
    trial_ref: str


@dataclass(frozen=True)
class TrialJob:
    """C's immutable job binding and available accounting for a trial."""

    job: JobRef
    binding: SelectedHost
    scenario_id: str
    fixture: bool
    real_model_calls: int | None = None


@dataclass(frozen=True)
class LoadObservation:
    observation_ref: str
    job: JobRef
    version_receipt_ref: str
    host_version: str | None
    mode: HostMode
    new_thread_ref: str | None
    loaded_version_ref: str | None
    load_state: Literal['loaded', 'not_loaded', 'unchecked', 'unavailable']
    behavior_state: Literal['met', 'not_met', 'unchecked']
    evidence_ref: str | None
    behavior_run_ref: str | None
    fixture: bool


@dataclass(frozen=True)
class TrialReceipt:
    trial_ref: str
    record: TrialRecord
    host_id: str
    comparison_verdict: str | None
    version_status: str | None
    load_state: str
    behavior_state: str
    real_model_calls: int | None


class TrialPort(Protocol):
    """Local C adapter for probes and immutable proof. No execution entry points."""

    def probe_host(self, scope: LearningScope, host_binding_ref: str) -> HostProbe: ...

    def read_trial_job(self, job: JobRef) -> TrialJob: ...

    def read_comparison(self, job: JobRef, report_ref: str) -> ComparisonReport: ...

    def read_run(self, job: JobRef, run_ref: str) -> RunRecord:
        """Read execution evidence, including the operation, session and version for post-load runs."""
        ...

    def read_version_receipt(self, job: JobRef, receipt_ref: str) -> VersionReceipt: ...

    def read_operation(self, operation: OperationRef) -> OperationView: ...

    def read_load_observation(self, job: JobRef, observation_ref: str) -> LoadObservation: ...
