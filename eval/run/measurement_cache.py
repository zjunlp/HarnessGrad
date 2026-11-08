"""Measurement identity keys and admitted cache lookup; no execution or cache writes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from harnessgrad import candidate_runtime, dataset_runtime_identity, verification_context
from eval.containers import external_runtime
from eval.run import chess_stepwise
from eval.verification.isolation import declared_class
from eval.verification.pristine import enforce_verdict_binding


@dataclass(frozen=True)
class MeasurementIdentity:
    harness_sha: str
    task_id: str
    trials: int
    context: dict | None
    dataset: dict | None
    harness: dict | None
    external: dict
    scorer_bundle: dict | None
    native: dict | None
    protocol: str | None

    def key(self) -> str:
        key = f'{self.harness_sha}:{self.task_id}:{self.trials}'
        if self.context is not None:
            key += ':verification-context:' + self.context['tasks'][self.task_id]
        if self.dataset is not None:
            key += ':dataset-runtime:' + hashlib.sha256(json.dumps(
                self.dataset, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if self.harness is not None:
            key += ':harness-runtime:' + self.harness['sha256']
        if self.external:
            key += ':external-runtime:' + hashlib.sha256(json.dumps(
                self.external, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if self.scorer_bundle is not None:
            key += ':scorer:' + hashlib.sha256(json.dumps(
                self.scorer_bundle, sort_keys=True, default=str).encode()).hexdigest()
        if self.native is not None:
            key += ':native:' + hashlib.sha256(json.dumps(
                self.native, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if self.protocol == chess_stepwise.STEPWISE:
            key += ':' + chess_stepwise.STEPWISE
        return key


@dataclass(frozen=True)
class CacheLookup:
    hit: dict | None = None
    invalid: dict | None = None


def native_cache_matches(entry: dict, expected: dict | None, trials: int) -> bool:
    """Keep cached native measurements bound to every admitted trial receipt."""
    if not isinstance(entry, dict):
        return False
    last = entry.get('verdict') or {}
    rows = entry.get('trial_verdicts') or [last]
    if not isinstance(rows, (list, tuple)):
        return False
    if expected is None:
        return not any(isinstance(row, dict) and 'native_runtime' in row for row in [last, *rows])
    return (expected.get('verified') is True and len(rows) == trials
            and isinstance(last, dict) and last.get('native_runtime') == expected
            and all(isinstance(row, dict) and row.get('native_runtime') == expected for row in rows))


def _matches_identities(hit, identity: MeasurementIdentity):
    if not native_cache_matches(hit, identity.native, identity.trials):
        return False
    try:
        external_runtime.require_records(identity.external, hit)
    except external_runtime.RuntimeUnavailable:
        return False
    if dataset_runtime_identity.problems(hit.get('dataset_runtime_identity'), identity.dataset):
        return False
    if (hit.get('verification_context') is None or
            verification_context.problems(hit.get('verification_context'), identity.context)):
        return False
    if candidate_runtime.problems(hit.get('harness_runtime_identity'), identity.harness):
        return False
    if identity.harness is not None:
        record = hit.get('harness_runtime')
        if not isinstance(record, dict) or candidate_runtime.problems(record.get('identity'), identity.harness):
            return False
    return True


def _pristine_error(hit, identity: MeasurementIdentity, *, verifier, run_id, trial_identity):
    if declared_class(verifier) != 'pristine-offline':
        return None
    cached = hit.get('trial_verdicts') or [hit.get('verdict') or {}]
    if len(cached) != identity.trials:
        return {'stage': 'check', 'detail': 'pristine cached verdict execution binding coverage mismatch'}
    for index, verdict in enumerate(cached):
        checked = enforce_verdict_binding(verdict, task_id=identity.task_id, run_id=run_id,
                                          trial_id=trial_identity(index))
        if checked.get('unmeasured'):
            return {'stage': 'check', 'detail': checked['unmeasured']}
    return None


def lookup(cache, identity: MeasurementIdentity, *, fresh, allowed, verifier, run_id, trial_identity):
    """Look up only after current admission; cached evidence is never fresh observation."""
    key = identity.key()
    hit = None if fresh or not allowed else cache.get(key)
    if hit is None or not _matches_identities(hit, identity):
        return CacheLookup()
    invalid = _pristine_error(hit, identity, verifier=verifier, run_id=run_id, trial_identity=trial_identity)
    return CacheLookup(invalid=invalid) if invalid is not None else CacheLookup(hit=hit)
