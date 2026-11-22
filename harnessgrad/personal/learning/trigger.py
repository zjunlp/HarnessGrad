"""Deterministic preflight for a reflection, with no model or job submission."""
from __future__ import annotations

from datetime import timedelta

from harnessgrad.personal.learning.contracts import SourceItem, TriggerDecision, TriggerInput


def ignore_reason(item: SourceItem) -> str | None:
    if item.own_event:
        return 'own_event'
    if item.request_kind == 'one_off':
        return 'one_off_request'
    if not item.task_boundary:
        return 'not_task_boundary'
    selection = item.selection
    if (item.event.role != 'user' or (selection is None and item.request_kind != 'unspecified')
            or (selection is not None and not item.event.text[selection.start:selection.end].strip())):
        return 'missing_evidence'
    if item.completeness == 'partial':
        return 'partial_evidence'
    if item.request_kind == 'unspecified':
        return 'unclassified_feedback'
    return None


def decide_trigger(request: TriggerInput) -> TriggerDecision:
    if request.scope != request.policy.scope:
        raise ValueError('learning policy is outside the trigger scope')
    if not request.policy.enabled:
        return TriggerDecision('skipped', reason_code='learning_disabled')
    if not request.evidence_refs:
        return TriggerDecision('skipped', reason_code='missing_evidence')
    if request.remaining_budget <= 0:
        return TriggerDecision('deferred', request.evidence_refs, 'budget_exhausted')
    if (request.last_trigger_at is not None
            and request.now - request.last_trigger_at < timedelta(seconds=request.policy.cooldown_seconds)):
        return TriggerDecision('deferred', request.evidence_refs, 'cooldown')
    return TriggerDecision('triggered', request.evidence_refs)
